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

import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MondayBoard(models.Model):
    """Representing Monday.com Boards."""
    _name = 'monday.board'
    _description = 'Monday Board'

    name = fields.Char(string='Board Name', required=True)
    instance_id = fields.Many2one('monday.instance', string='Monday Instance', ondelete='cascade', required=True)
    monday_board_id = fields.Char(string='Monday Board ID', required=True)
    board_type = fields.Selection([
        ('public', 'Public'),
        ('private', 'Private'),
        ('shareable', 'Shareable')
    ], string='Board Type', default='public')
    odoo_model_id = fields.Many2one('ir.model', string='Mapped Odoo Model', help="The primary Odoo model synchronized with this board.")
    odoo_project_id = fields.Many2one('project.project', string='Odoo Project', help="Odoo Project mapped to this board (applicable for Tasks).")
    column_ids = fields.One2many('monday.board.column', 'board_id', string='Board Columns')
    group_ids = fields.One2many('monday.group', 'board_id', string='Board Groups')
    active = fields.Boolean(string='Active', default=True)

    _sql_constraints = [
        ('uniq_board_instance', 'unique(monday_board_id, instance_id)', 'Board ID must be unique per instance!')
    ]

    def action_import_columns(self):
        """Imports columns of the board from Monday.com."""
        self.ensure_one()
        self = self.with_context(monday_sync_direction='import')
        query = """
        query ($board_id: [ID!]) {
          boards (ids: $board_id) {
            columns {
              id
              title
              type
            }
          }
        }
        """
        try:
            res = self.instance_id.execute_graphql(query, {"board_id": [int(self.monday_board_id)]})
            boards = res.get("boards", [])
            if not boards:
                raise UserError(_("Board not found in Monday.com"))

            columns = boards[0].get("columns", [])
            existing_cols = {c.monday_column_id: c for c in self.column_ids}

            for col in columns:
                col_id = col["id"]
                col_title = col["title"]
                col_type = col["type"]

                if col_id in existing_cols:
                    existing_cols[col_id].write({
                        'name': col_title,
                        'column_type': col_type
                    })
                else:
                    self.env['monday.board.column'].create({
                        'board_id': self.id,
                        'name': col_title,
                        'monday_column_id': col_id,
                        'column_type': col_type
                    })
        except Exception as e:
            raise UserError(_("Failed to import columns: %s") % str(e))
        return True

    def action_import_groups(self):
        """Imports groups of the board from Monday.com."""
        self.ensure_one()
        self = self.with_context(monday_sync_direction='import')
        try:
            groups = self.instance_id.get_groups(self.monday_board_id)
            existing_groups = {g.monday_group_id: g for g in self.group_ids}

            for grp in groups:
                g_id = grp["id"]
                g_title = grp["title"]

                if g_id in existing_groups:
                    existing_groups[g_id].write({'name': g_title})
                else:
                    self.env['monday.group'].create({
                        'board_id': self.id,
                        'name': g_title,
                        'monday_group_id': g_id
                    })
        except Exception as e:
            raise UserError(_("Failed to import groups: %s") % str(e))
        return True

    def action_auto_map_fields(self):
        """Automatically maps board columns to Odoo model fields based on name and type similarity."""
        self.ensure_one()
        if not self.odoo_model_id:
            raise UserError(_("Please configure the Mapped Odoo Model first."))
        if not self.column_ids:
            # Try importing columns first
            self.action_import_columns()
            if not self.column_ids:
                raise UserError(_("No columns found to map. Please import columns first."))

        fields_obj = self.env['ir.model.fields'].search([('model_id', '=', self.odoo_model_id.id)])
        fields_by_name = {f.name: f for f in fields_obj}
        fields_by_desc = {f.field_description.lower(): f for f in fields_obj if f.field_description}

        mapping_count = 0
        existing_mappings = self.env['monday.mapping'].search([
            ('monday_board_id', '=', self.id)
        ])
        existing_mapped_cols = set(existing_mappings.mapped('monday_column_id'))

        for col in self.column_ids:
            if col.monday_column_id in existing_mapped_cols:
                continue

            target_field = None
            col_name_lower = col.name.lower()
            col_id_lower = col.monday_column_id.lower()

            # 1. Match by exact Odoo field name
            if col_id_lower in fields_by_name:
                target_field = fields_by_name[col_id_lower]
            elif col_name_lower in fields_by_name:
                target_field = fields_by_name[col_name_lower]
            # 2. Match by field description (label)
            elif col_name_lower in fields_by_desc:
                target_field = fields_by_desc[col_name_lower]
            # 3. Smart fallbacks for common fields
            else:
                if col.column_type == 'email':
                    target_field = fields_by_name.get('email_from') or fields_by_name.get('email')
                elif col.column_type == 'phone':
                    target_field = fields_by_name.get('phone') or fields_by_name.get('mobile')
                elif col_name_lower in ['name', 'title', 'subject']:
                    target_field = fields_by_name.get('name') or fields_by_name.get('display_name')
                elif col.column_type == 'people':
                    target_field = fields_by_name.get('user_id') or fields_by_name.get('user_ids')
                elif col.column_type == 'date':
                    target_field = fields_by_name.get('date_deadline') or fields_by_name.get('date')
                elif col_name_lower in ['stage', 'status', 'state']:
                    target_field = fields_by_name.get('stage_id') or fields_by_name.get('state')

            if target_field:
                mapped_type = col.column_type
                if mapped_type == 'name':
                    mapped_type = 'text'
                elif mapped_type == 'color':
                    mapped_type = 'status'
                elif mapped_type == 'file':
                    mapped_type = 'files'

                valid_types = dict(self.env['monday.mapping']._fields['monday_column_type'].selection)
                if mapped_type not in valid_types:
                    mapped_type = 'text'

                self.env['monday.mapping'].create({
                    'instance_id': self.instance_id.id,
                    'odoo_model_id': self.odoo_model_id.id,
                    'odoo_field_id': target_field.id,
                    'monday_board_id': self.id,
                    'monday_column_id': col.monday_column_id,
                    'monday_column_type': mapped_type,
                    'direction': 'two_way'
                })
                mapping_count += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Auto Map Fields'),
                'message': _('Successfully auto-generated %s field mappings.') % mapping_count,
                'sticky': False,
                'type': 'success',
            }
        }

    def action_configure_webhooks(self):
        """Automatically registers webhooks on Monday.com for this board."""
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        if not base_url:
            raise UserError(_("Please configure the system parameter 'web.base.url'."))

        base_url = base_url.rstrip('/')
        webhook_url = f"{base_url}/monday/webhook/{self.instance_id.id}".strip()

        # Validate that the Odoo base URL is not a local/private address
        from urllib.parse import urlparse
        parsed_url = urlparse(webhook_url)
        hostname = parsed_url.hostname or ''
        if hostname in ['localhost', '127.0.0.1', '0.0.0.0'] or hostname.startswith('192.168.') or hostname.startswith('10.'):
            raise UserError(_(
                "Your Odoo instance is currently using a local address (%s). "
                "Monday.com cannot send webhooks to local/private addresses. "
                "Please configure a public URL (e.g., using ngrok, Hookdeck, or your public domain) "
                "in your Odoo System Parameters ('web.base.url') under Settings > Technical > System Parameters "
                "before configuring webhooks."
            ) % hostname)

        events = [
            'create_item',
            'change_column_value',
            'change_subitem_column_value',
            'item_moved_to_any_group',
            'item_archived',
            'item_deleted'
        ]

        success_events = []
        failed_events = []

        for event in events:
            try:
                res = self.instance_id.create_webhook(
                    board_id=self.monday_board_id,
                    url=webhook_url,
                    event=event
                )
                if res and res.get('id'):
                    success_events.append(event)
            except Exception as e:
                if event == 'change_subitem_column_value':
                    _logger.warning("Skipped registering change_subitem_column_value webhook on board %s: %s", self.name, str(e))
                else:
                    failed_events.append(f"{event} ({str(e)})")

        if failed_events and not success_events:
            raise UserError(_("Failed to configure webhooks: %s") % ", ".join(failed_events))

        message = _("Successfully registered webhooks for: %s.") % ", ".join(success_events)
        if failed_events:
            message += _(" Failed for: %s.") % ", ".join(failed_events)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Webhook Configuration'),
                'message': message,
                'sticky': True,
                'type': 'warning' if failed_events else 'success',
            }
        }


class MondayBoardColumn(models.Model):
    """Stores board column structure metadata for mapping configuration."""
    _name = 'monday.board.column'
    _description = 'Monday Board Column'

    board_id = fields.Many2one('monday.board', string='Board', required=True, ondelete='cascade')
    name = fields.Char(string='Column Name', required=True)
    monday_column_id = fields.Char(string='Column ID', required=True)
    column_type = fields.Char(string='Column Type', required=True)
