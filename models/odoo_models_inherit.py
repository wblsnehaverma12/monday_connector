# -*- coding: utf-8 -*-
#
#################################################################################
# Author      : Weblytic Labs Pvt. Ltd. (<https://store.weblyticlabs.com/>)
# Copyright(c): 2023-Present Weblytic Labs Pvt. Ltd.
# All Rights Reserved.
#
# This program is copyright property of the author mentioned above.
# You can`t redistribute it and/or modify it.
##################################################################################

from odoo import models, fields, api


def enqueue_monday_sync(record, operation, changed_fields=None):
    """Utility to enqueue Odoo database changes into the Monday queue job table."""
    if record.env.context.get('from_monday_webhook') or record.env.context.get('monday_sync_direction') == 'import':
        return

    # Find active Monday instances
    instances = record.env['monday.instance'].sudo().search([('active', '=', True)])
    if not instances:
        return

    # Filter out system and tracking fields
    if changed_fields:
        ignored_fields = {
            'write_date', 'write_uid', 'message_ids', 'activity_ids',
            'message_follower_ids', 'access_token', 'access_warning',
            'monday_instance_id', 'monday_item_id'
        }
        filtered_fields = [f for f in changed_fields if f not in ignored_fields]
        if not filtered_fields:
            return
        changed_fields = filtered_fields

    import json
    for instance in instances:
        model_name = record._name

        if model_name == 'project.project':
            op_type = 'sync_project'
        elif model_name == 'project.milestone':
            op_type = 'sync_milestone'
        else:
            op_type = operation

        # Check mapping status for regular CRM/Task/Partner items
        if op_type in ['create', 'update', 'delete']:
            mapping = record.env['monday.record.mapping'].sudo().search([
                ('instance_id', '=', instance.id),
                ('odoo_model', '=', model_name),
                ('odoo_id', '=', record.id)
            ], limit=1)
            has_mapping = bool(mapping and mapping.monday_item_id)

            # If no mapping, we only allow 'create' operation.
            # Any 'update' is redundant because the initial 'create' job will sync latest data.
            if not has_mapping and op_type == 'update':
                continue

        # Check for existing pending job for this record and operation
        existing_job = record.env['monday.queue.job'].sudo().search([
            ('instance_id', '=', instance.id),
            ('model_name', '=', model_name),
            ('record_id', '=', record.id),
            ('state', '=', 'pending'),
            ('operation', '=', op_type)
        ], limit=1)

        if existing_job:
            # If it's an update, merge changed fields to avoid losing field updates
            if op_type == 'update' and changed_fields:
                try:
                    payload = json.loads(existing_job.payload or '{}')
                    existing_fields = payload.get('changed_fields') or []
                    merged_fields = list(set(existing_fields + changed_fields))
                    payload['changed_fields'] = merged_fields
                    existing_job.payload = json.dumps(payload)
                except Exception:
                    pass
            # Job already exists/merged, don't create a new one
            continue

        # If it's an update, but a pending 'create' job exists, skip the update job
        if op_type == 'update':
            pending_create = record.env['monday.queue.job'].sudo().search([
                ('instance_id', '=', instance.id),
                ('model_name', '=', model_name),
                ('record_id', '=', record.id),
                ('state', '=', 'pending'),
                ('operation', '=', 'create')
            ], limit=1)
            if pending_create:
                continue

        # Enqueue the job
        payload_dict = None
        if op_type == 'update':
            payload_dict = {'changed_fields': changed_fields}
        elif op_type == 'delete':
            # For delete, retrieve monday_item_id before record is unlinked
            mapping = record.env['monday.record.mapping'].sudo().search([
                ('instance_id', '=', instance.id),
                ('odoo_model', '=', model_name),
                ('odoo_id', '=', record.id)
            ], limit=1)
            m_item_id = mapping.monday_item_id if mapping else None
            payload_dict = {'monday_item_id': m_item_id}

        instance.env['monday.queue.job'].enqueue(
            instance=instance,
            model_name=model_name,
            record_id=record.id,
            operation=op_type,
            payload_dict=payload_dict
        )



class ResPartner(models.Model):
    _inherit = 'res.partner'

    @api.model_create_multi
    def create(self, vals_list):
        records = super(ResPartner, self).create(vals_list)
        for record in records:
            enqueue_monday_sync(record, 'create')
        return records

    def write(self, vals):
        res = super(ResPartner, self).write(vals)
        for record in self:
            enqueue_monday_sync(record, 'update', list(vals.keys()))
        return res

    def unlink(self):
        for record in self:
            enqueue_monday_sync(record, 'delete')
        return super(ResPartner, self).unlink()


class ProjectProject(models.Model):
    _inherit = 'project.project'

    @api.model_create_multi
    def create(self, vals_list):
        records = super(ProjectProject, self).create(vals_list)
        for record in records:
            enqueue_monday_sync(record, 'create')  # Will enqueue sync_project
        return records

    def write(self, vals):
        res = super(ProjectProject, self).write(vals)
        for record in self:
            enqueue_monday_sync(record, 'update', list(vals.keys()))
        return res

    def unlink(self):
        for record in self:
            enqueue_monday_sync(record, 'delete')
        return super(ProjectProject, self).unlink()


class ProjectTask(models.Model):
    _inherit = 'project.task'

    monday_instance_id = fields.Many2one(
        'monday.instance', 
        string='Monday Account', 
        compute='_compute_monday_details', 
        inverse='_inverse_monday_details',
        store=False
    )
    monday_item_id = fields.Char(
        string='Monday Item ID', 
        compute='_compute_monday_details', 
        inverse='_inverse_monday_details',
        store=False
    )

    def _compute_monday_details(self):
        for record in self:
            mapping = self.env['monday.record.mapping'].sudo().search([
                ('odoo_model', '=', record._name),
                ('odoo_id', '=', record.id),
                ('monday_item_id', '!=', False)
            ], limit=1)
            if mapping:
                record.monday_instance_id = mapping.instance_id
                record.monday_item_id = mapping.monday_item_id
            else:
                record.monday_instance_id = False
                record.monday_item_id = False

    def _inverse_monday_details(self):
        for record in self:
            mapping = self.env['monday.record.mapping'].sudo().search([
                ('odoo_model', '=', record._name),
                ('odoo_id', '=', record.id),
                ('monday_item_id', '!=', False)
            ], limit=1)
            if record.monday_instance_id and record.monday_item_id:
                vals = {
                    'instance_id': record.monday_instance_id.id,
                    'monday_item_id': record.monday_item_id,
                    'odoo_model': record._name,
                    'odoo_id': record.id,
                    'sync_status': 'synced',
                    'last_sync': fields.Datetime.now()
                }
                # Find board_id if possible
                if record.project_id:
                    board_map = self.env['monday.record.mapping'].sudo().search([
                        ('instance_id', '=', record.monday_instance_id.id),
                        ('odoo_model', '=', 'project.project'),
                        ('odoo_id', '=', record.project_id.id)
                    ], limit=1)
                    if board_map:
                        vals['monday_board_id'] = board_map.monday_board_id.id

                if mapping:
                    mapping.write(vals)
                else:
                    self.env['monday.record.mapping'].sudo().create(vals)
            else:
                if mapping:
                    mapping.unlink()

    @api.model_create_multi
    def create(self, vals_list):
        records = super(ProjectTask, self).create(vals_list)
        for record in records:
            enqueue_monday_sync(record, 'create')
        return records

    def write(self, vals):
        res = super(ProjectTask, self).write(vals)
        for record in self:
            enqueue_monday_sync(record, 'update', list(vals.keys()))
        return res

    def unlink(self):
        for record in self:
            enqueue_monday_sync(record, 'delete')
        return super(ProjectTask, self).unlink()


class ProjectMilestone(models.Model):
    _inherit = 'project.milestone'

    @api.model_create_multi
    def create(self, vals_list):
        records = super(ProjectMilestone, self).create(vals_list)
        for record in records:
            enqueue_monday_sync(record, 'create')  # Will enqueue sync_milestone
        return records

    def write(self, vals):
        res = super(ProjectMilestone, self).write(vals)
        for record in self:
            enqueue_monday_sync(record, 'update', list(vals.keys()))
        return res

    def unlink(self):
        for record in self:
            enqueue_monday_sync(record, 'delete')
        return super(ProjectMilestone, self).unlink()


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.model_create_multi
    def create(self, vals_list):
        records = super(SaleOrder, self).create(vals_list)
        for record in records:
            enqueue_monday_sync(record, 'create')
        return records

    def write(self, vals):
        res = super(SaleOrder, self).write(vals)
        for record in self:
            enqueue_monday_sync(record, 'update', list(vals.keys()))
        return res

    def unlink(self):
        for record in self:
            enqueue_monday_sync(record, 'delete')
        return super(SaleOrder, self).unlink()



class ProjectTaskType(models.Model):
    _inherit = 'project.task.type'

    monday_instance_id = fields.Many2one('monday.instance', string='Monday Account')
    monday_group_id = fields.Char(string='Monday Group ID', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        records = super(ProjectTaskType, self).create(vals_list)
        for record in records:
            if record.monday_instance_id:
                record._sync_to_monday()
        return records

    def write(self, vals):
        res = super(ProjectTaskType, self).write(vals)
        if 'name' in vals or 'monday_instance_id' in vals or 'project_ids' in vals:
            for record in self:
                if record.monday_instance_id:
                    record._sync_to_monday()
        return res

    def _sync_to_monday(self):
        """Creates/syncs this stage as a group on mapped boards of linked projects."""
        self.ensure_one()
        instance = self.monday_instance_id
        for project in self.project_ids:
            board = self.env['monday.board'].sudo().search([
                ('instance_id', '=', instance.id),
                ('odoo_model_id.model', '=', 'project.task'),
                ('odoo_project_id', '=', project.id)
            ], limit=1)
            if not board:
                continue

            try:
                if not self.monday_group_id:
                    mutation = """
                    mutation ($board_id: ID!, $group_name: String!) {
                      create_group (board_id: $board_id, group_name: $group_name) {
                        id
                      }
                    }
                    """
                    res = instance.execute_graphql(mutation, {
                        'board_id': int(board.monday_board_id),
                        'group_name': self.name
                    })
                    if res and res.get('create_group') and res['create_group'].get('id'):
                        self.write({'monday_group_id': res['create_group']['id']})
            except Exception as e:
                from logging import getLogger
                getLogger(__name__).error("Failed to sync project stage to Monday: %s", str(e))
