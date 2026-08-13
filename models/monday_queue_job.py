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

import json
import logging
import traceback
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class MondayQueueJob(models.Model):
    """Asynchronous queue to process outbound integrations without blocking UI."""
    _name = 'monday.queue.job'
    _description = 'Monday Queue Job'
    _order = 'create_date asc'

    name = fields.Char(string='Summary', required=True)
    instance_id = fields.Many2one('monday.instance', string='Instance', required=True, ondelete='cascade')
    model_name = fields.Char(string='Odoo Model', required=True)
    record_id = fields.Integer(string='Record ID', required=True)
    operation = fields.Selection([
        ('create', 'Create Item'),
        ('update', 'Update Item'),
        ('delete', 'Delete Item'),
        ('archive', 'Archive Item'),
        ('sync_project', 'Sync Project to Board'),
        ('sync_milestone', 'Sync Milestone to Group'),
        ('upload_file', 'Upload File Attachment'),
        ('import_entity', 'Import Entity Data')
    ], string='Operation', required=True)
    payload = fields.Text(string='Payload (JSON)')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('pending', 'Pending'),
        ('done', 'Done'),
        ('failed', 'Failed')
    ], string='Status', default='pending', index=True)
    retry_count = fields.Integer(string='Retry Count', default=0)
    error_message = fields.Text(string='Error Message')
    traceback = fields.Text(string='Traceback')
    active = fields.Boolean(string='Active', default=True)

    @api.model
    def enqueue(self, instance, model_name, record_id, operation, payload_dict=None):
        """Helper to enqueue a job."""
        val = {
            'name': f"{operation.replace('_', ' ').title()} - {model_name} ({record_id})",
            'instance_id': instance.id,
            'model_name': model_name,
            'record_id': record_id,
            'operation': operation,
            'payload': json.dumps(payload_dict or {}),
            'state': 'pending',
        }
        return self.create(val)

    def process_queue(self):
        """Cron job entry point. Processes pending jobs."""
        jobs = self.search([('state', '=', 'pending')], limit=50)
        for job in jobs:
            job._process_job()

    def retry_failed_jobs(self):
        """Cron job to retry failed jobs that haven't exceeded retry limit."""
        jobs = self.search([
            ('state', '=', 'failed'),
            ('retry_count', '<', 5)
        ])
        jobs.write({'state': 'pending'})
        return len(jobs)

    def action_process_job(self):
        """Force process the queue job from UI button."""
        self._process_job()

    def _process_job(self):
        """Process a single queue job."""
        self.ensure_one()
        self.state = 'pending'
        instance = self.instance_id

        if not instance.active:
            self.write({
                'state': 'failed',
                'error_message': 'Instance is inactive.'
            })
            return

        try:
            payload = json.loads(self.payload or '{}')
            model_name = self.model_name
            record_id = self.record_id
            operation = self.operation
            if operation == 'import_entity':
                instance._process_background_import(payload)
                self.write({'state': 'done', 'error_message': False, 'traceback': False})
                return

            # 1. Handle Delete/Archive (Odoo record may no longer exist)
            if operation in ['delete', 'archive']:
                monday_item_id = payload.get('monday_item_id')
                if not monday_item_id:
                    # Look up in record mapping
                    mapping = self.env['monday.record.mapping'].search([
                        ('instance_id', '=', instance.id),
                        ('odoo_model', '=', model_name),
                        ('odoo_id', '=', record_id),
                        ('monday_item_id', '!=', False)
                    ], limit=1)
                    if mapping:
                        monday_item_id = mapping.monday_item_id

                if monday_item_id:
                    if operation == 'delete':
                        instance.delete_item(monday_item_id)
                    else:
                        instance.archive_item(monday_item_id)

                    # Delete the record mappings
                    mappings = self.env['monday.record.mapping'].search([
                        ('instance_id', '=', instance.id),
                        ('odoo_model', '=', model_name),
                        ('odoo_id', '=', record_id)
                    ])
                    mappings.unlink()

                self.write({'state': 'done', 'error_message': False, 'traceback': False})
                return

            # 2. Operations requiring active Odoo Record
            record = self.env[model_name].with_context(active_test=False).browse(record_id)
            if not record.exists():
                self.write({
                    'state': 'failed',
                    'error_message': f"Odoo record {model_name} ({record_id}) does not exist."
                })
                return

            # Handle Project/Board creation/mapping
            if operation == 'sync_project':
                instance._sync_project_to_board(record)
                self.write({'state': 'done', 'error_message': False})
                return

            # Handle Group/Milestone synchronization
            if operation == 'sync_milestone':
                # Group creation in Monday
                instance._sync_milestone_to_group(record, payload)
                self.write({'state': 'done', 'error_message': False})
                return

            # Handle file attachments upload
            if operation == 'upload_file':
                attachment_id = payload.get('attachment_id')
                column_id = payload.get('monday_column_id')
                if attachment_id:
                    attachment = self.env['ir.attachment'].browse(attachment_id)
                    if attachment.exists():
                        instance._upload_file_to_item(record, attachment, column_id)
                self.write({'state': 'done', 'error_message': False})
                return

            # Handle regular CRM/Task/Partner Item Creation or Update
            if operation == 'create':
                instance._process_item_create(record)
            elif operation == 'update':
                instance._process_item_update(record, payload.get('changed_fields'))

            self.write({'state': 'done', 'error_message': False, 'traceback': False})

        except Exception as e:
            _logger.exception("Error processing Monday queue job ID %s", self.id)
            tb = traceback.format_exc()
            self.write({
                'retry_count': self.retry_count + 1,
                'state': 'failed' if self.retry_count >= 4 else 'pending', # Max 5 attempts
                'error_message': str(e),
                'traceback': tb
            })
