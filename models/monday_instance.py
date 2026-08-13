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
import requests
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from ..utils.graphql_client import GraphQLClient

_logger = logging.getLogger(__name__)


class MondayInstance(models.Model):
    """Configuration model for Monday.com integration."""
    _name = 'monday.instance'
    _description = 'Monday Instance'

    name = fields.Char(string='Name', required=True)
    api_token = fields.Char(string='API Token', required=True, password=True)
    api_version = fields.Selection([
        ('2023-10', '2023-10'),
        ('2024-01', '2024-01'),
        ('2024-04', '2024-04'),
        ('2024-07', '2024-07'),
        ('2024-10', '2024-10'),
    ], string='API Version', default='2024-01', required=True)
    base_url = fields.Char(string='Base URL', default='https://api.monday.com/v2', required=True)
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company, required=True)
    active = fields.Boolean(string='Active', default=True)
    webhook_secret = fields.Char(string='Webhook Secret', help="Secret token used to validate webhook authenticity.")
    sync_interval = fields.Selection([
        ('1_min', '1 Minute'),
        ('5_min', '5 Minutes'),
        ('15_min', '15 Minutes'),
        ('hourly', 'Hourly'),
    ], string='Sync Interval', default='5_min', required=True)
    last_sync = fields.Datetime(string='Last Sync')
    status = fields.Selection([
        ('draft', 'Draft'),
        ('connected', 'Connected'),
        ('error', 'Connection Error')
    ], string='Status', default='draft', readonly=True)
    monday_account_id = fields.Char(string='Account ID', readonly=True)
    import_users = fields.Boolean(string='Import Users', default=False)
    import_boards = fields.Boolean(string='Import Boards', default=False)
    import_groups = fields.Boolean(string='Import Groups', default=False)
    import_items = fields.Boolean(string='Import Items', default=False)

    boards_count = fields.Integer(string='Boards Count', compute='_compute_counts')
    users_count = fields.Integer(string='Users Count', compute='_compute_counts')
    items_count = fields.Integer(string='Items Count', compute='_compute_counts')

    def _compute_counts(self):
        for rec in self:
            rec.boards_count = self.env['monday.board'].search_count([('instance_id', '=', rec.id)])
            rec.users_count = self.env['res.partner'].search_count([('comment', 'like', 'Imported from Monday.com User ID:%')])
            rec.items_count = self.env['monday.record.mapping'].search_count([
                ('instance_id', '=', rec.id),
                ('monday_item_id', '!=', False)
            ])

    def connect(self):
        """Returns initialized GraphQLClient with log callback mapping to monday.sync.log."""
        self.ensure_one()
        direction = self.env.context.get('monday_sync_direction', 'export')

        def log_cb(query, variables, response_text, duration, status, error_msg=None):
            # Create a separate log record using a new cursor or new transaction helper
            # Standard Odoo log creation:
            try:
                self.env['monday.sync.log'].sudo().create({
                    'name': f"GraphQL Query - {status.title()}",
                    'instance_id': self.id,
                    'direction': direction,
                    'operation': 'graphql_request',
                    'request_data': json.dumps({'query': query, 'variables': variables or {}}),
                    'response_data': response_text,
                    'duration': duration,
                    'status': status,
                    'error_message': error_msg
                })
            except Exception as le:
                _logger.error("Failed to write Monday sync log: %s", str(le))

        return GraphQLClient(
            api_token=self.api_token,
            api_version=self.api_version,
            base_url=self.base_url,
            log_callback=log_cb
        )

    def test_connection(self):
        """Verifies the connection to Monday.com by retrieving current user details."""
        for rec in self:
            try:
                client = rec.connect()
                query = """
                query {
                  me { id name email }
                  account { id name }
                }
                """
                res = client.execute(query)
                if res and res.get('me'):
                    vals = {'status': 'connected'}
                    if res.get('account') and res['account'].get('id'):
                        vals['monday_account_id'] = str(res['account']['id'])
                    rec.write(vals)
                    # Post message in chatter if mail.thread is used
                    _logger.info("Connection test succeeded for Monday instance %s", rec.name)
                else:
                    rec.write({'status': 'error'})
            except Exception as e:
                rec.write({'status': 'error'})
                raise UserError(_("Connection Test Failed: %s") % str(e))
        return True

    def execute_graphql(self, query, variables=None):
        """Helper to run a query/mutation directly."""
        self.ensure_one()
        client = self.connect()
        return client.execute(query, variables)

    # ==========================================
    # BOARD MANAGEMENT METHODS
    # ==========================================

    def create_board(self, name, board_type="public"):
        """Creates a board in Monday.com and returns the board ID."""
        self.ensure_one()
        m_board_kind = 'share' if board_type == 'shareable' else board_type
        query = """
        mutation ($board_name: String!, $board_kind: BoardKind!) {
          create_board (board_name: $board_name, board_kind: $board_kind) {
            id
          }
        }
        """
        res = self.execute_graphql(query, {"board_name": name, "board_kind": m_board_kind})
        return res.get("create_board", {}).get("id")

    def archive_board(self, board_id):
        """Archives a board in Monday.com."""
        self.ensure_one()
        query = """
        mutation ($board_id: ID!) {
          archive_board (board_id: $board_id) {
            id
          }
        }
        """
        res = self.execute_graphql(query, {"board_id": str(board_id)})
        return res.get("archive_board", {}).get("id")

    def duplicate_board(self, board_id, duplicate_type="with_structure"):
        """Duplicates a board in Monday.com."""
        self.ensure_one()
        query = """
        mutation ($board_id: ID!, $duplicate_type: BoardDuplicateType!) {
          duplicate_board (board_id: $board_id, duplicate_type: $duplicate_type) {
            board {
              id
            }
          }
        }
        """
        res = self.execute_graphql(query, {"board_id": int(board_id), "duplicate_type": duplicate_type})
        return res.get("duplicate_board", {}).get("board", {}).get("id")

    # ==========================================
    # GROUP MANAGEMENT METHODS
    # ==========================================

    def get_groups(self, board_id):
        """Retrieves list of groups for a specific board."""
        self.ensure_one()
        query = """
        query ($board_id: [ID!]) {
          boards (ids: $board_id) {
            groups {
              id
              title
            }
          }
        }
        """
        res = self.execute_graphql(query, {"board_id": [int(board_id)]})
        boards = res.get("boards", [])
        if boards:
            return boards[0].get("groups", [])
        return []

    def create_group(self, board_id, group_name):
        """Creates a group within a board in Monday.com."""
        self.ensure_one()
        query = """
        mutation ($board_id: ID!, $group_name: String!) {
          create_group (board_id: $board_id, group_name: $group_name) {
            id
          }
        }
        """
        res = self.execute_graphql(query, {"board_id": int(board_id), "group_name": group_name})
        return res.get("create_group", {}).get("id")

    def update_group(self, board_id, group_id, group_name):
        """Updates group title in Monday.com."""
        # Note: Depending on Monday API version, changing group title uses update_group mutation
        self.ensure_one()
        query = """
        mutation ($board_id: ID!, $group_id: String!, $group_name: String!) {
          update_group (board_id: $board_id, group_id: $group_id, group_attribute: title, new_value: $group_name) {
            id
          }
        }
        """
        res = self.execute_graphql(query, {
            "board_id": int(board_id),
            "group_id": group_id,
            "group_name": group_name
        })
        return res.get("update_group", {}).get("id")

    def delete_group(self, board_id, group_id):
        """Deletes a group within a board in Monday.com."""
        self.ensure_one()
        query = """
        mutation ($board_id: ID!, $group_id: String!) {
          delete_group (board_id: $board_id, group_id: $group_id) {
            id
          }
        }
        """
        res = self.execute_graphql(query, {"board_id": int(board_id), "group_id": group_id})
        return res.get("delete_group", {}).get("id")

    # ==========================================
    # ITEM MANAGEMENT METHODS
    # ==========================================

    def create_item(self, board_id, group_id, item_name, column_values=None):
        """Creates an item on a Monday board."""
        self.ensure_one()
        query = """
        mutation ($board_id: ID!, $group_id: String!, $item_name: String!, $column_values: JSON) {
          create_item (board_id: $board_id, group_id: $group_id, item_name: $item_name, column_values: $column_values) {
            id
          }
        }
        """
        vars_payload = {
            "board_id": int(board_id),
            "group_id": group_id,
            "item_name": item_name
        }
        if column_values:
            vars_payload["column_values"] = json.dumps(column_values)

        try:
            res = self.execute_graphql(query, vars_payload)
            return res.get("create_item", {}).get("id")
        except Exception as e:
            if column_values and ("status label" in str(e) or "doesn't exist" in str(e) or "Column value" in str(e) or "invalid" in str(e).lower()):
                _logger.warning("Failed to create item with status values: %s. Retrying without status columns...", str(e))
                # Find status columns for this board and remove them
                status_cols = self.env['monday.board.column'].sudo().search([
                    ('board_id.monday_board_id', '=', str(board_id)),
                    ('column_type', '=', 'status')
                ]).mapped('monday_column_id')
                
                cleaned_column_values = {k: v for k, v in column_values.items() if k not in status_cols}
                vars_payload["column_values"] = json.dumps(cleaned_column_values)
                try:
                    res = self.execute_graphql(query, vars_payload)
                    return res.get("create_item", {}).get("id")
                except Exception as e2:
                    # If it still fails, retry with no column values at all
                    _logger.warning("Failed to create item with cleaned column values: %s. Retrying with name only...", str(e2))
                    vars_payload.pop("column_values", None)
                    res = self.execute_graphql(query, vars_payload)
                    return res.get("create_item", {}).get("id")
            else:
                raise e

    def update_item(self, board_id, item_id, column_values):
        """Updates multiple column values on a Monday item."""
        self.ensure_one()
        query = """
        mutation ($board_id: ID!, $item_id: ID!, $column_values: JSON!) {
          change_multiple_column_values (board_id: $board_id, item_id: $item_id, column_values: $column_values) {
            id
          }
        }
        """
        vars_payload = {
            "board_id": int(board_id),
            "item_id": int(item_id),
            "column_values": json.dumps(column_values)
        }
        try:
            res = self.execute_graphql(query, vars_payload)
            return res.get("change_multiple_column_values", {}).get("id")
        except Exception as e:
            if "status label" in str(e) or "doesn't exist" in str(e) or "Column value" in str(e) or "invalid" in str(e).lower():
                _logger.warning("Failed to update item with status values: %s. Retrying without status columns...", str(e))
                # Find status columns for this board and remove them
                status_cols = self.env['monday.board.column'].sudo().search([
                    ('board_id.monday_board_id', '=', str(board_id)),
                    ('column_type', '=', 'status')
                ]).mapped('monday_column_id')
                
                cleaned_column_values = {k: v for k, v in column_values.items() if k not in status_cols}
                if cleaned_column_values:
                    vars_payload["column_values"] = json.dumps(cleaned_column_values)
                    try:
                        res = self.execute_graphql(query, vars_payload)
                        return res.get("change_multiple_column_values", {}).get("id")
                    except Exception as e2:
                        _logger.warning("Failed to update item with cleaned column values: %s. Skipping update...", str(e2))
                        return None
                else:
                    return None
            else:
                raise e

    def delete_item(self, item_id):
        """Deletes an item from Monday.com."""
        self.ensure_one()
        query = """
        mutation ($item_id: ID!) {
          delete_item (item_id: $item_id) {
            id
          }
        }
        """
        res = self.execute_graphql(query, {"item_id": int(item_id)})
        return res.get("delete_item", {}).get("id")

    def archive_item(self, item_id):
        """Archives an item in Monday.com."""
        self.ensure_one()
        query = """
        mutation ($item_id: ID!) {
          archive_item (item_id: $item_id) {
            id
          }
        }
        """
        res = self.execute_graphql(query, {"item_id": int(item_id)})
        return res.get("archive_item", {}).get("id")

    def move_item(self, board_id, group_id, item_id):
        """Moves an item to another group on the board."""
        self.ensure_one()
        query = """
        mutation ($board_id: ID!, $group_id: String!, $item_id: ID!) {
          move_item_to_group (board_id: $board_id, group_id: $group_id, item_id: $item_id) {
            id
          }
        }
        """
        res = self.execute_graphql(query, {
            "board_id": int(board_id),
            "group_id": group_id,
            "item_id": int(item_id)
        })
        return res.get("move_item_to_group", {}).get("id")

    def read_item(self, item_id):
        """Reads columns and structure of a specific Monday item."""
        self.ensure_one()
        query = """
        query ($item_id: [ID!]) {
          items (ids: $item_id) {
            id
            name
            board {
              id
            }
            group {
              id
            }
            column_values {
              id
              text
              value
              type
            }
          }
        }
        """
        res = self.execute_graphql(query, {"item_id": [int(item_id)]})
        items = res.get("items", [])
        return items[0] if items else None

    def read_updates(self, item_id):
        """Gets posts/updates from a Monday.com item."""
        self.ensure_one()
        query = """
        query ($item_id: [ID!]) {
          items (ids: $item_id) {
            updates {
              id
              body
              created_at
              creator {
                name
                email
              }
            }
          }
        }
        """
        res = self.execute_graphql(query, {"item_id": [int(item_id)]})
        items = res.get("items", [])
        return items[0].get("updates", []) if items else []

    def post_update(self, item_id, update_body):
        """Creates an update post on a Monday item."""
        self.ensure_one()
        query = """
        mutation ($item_id: ID!, $body: String!) {
          create_update (item_id: $item_id, body: $body) {
            id
          }
        }
        """
        res = self.execute_graphql(query, {"item_id": int(item_id), "body": update_body})
        return res.get("create_update", {}).get("id")

    # ==========================================
    # WEBHOOK REGISTRATION METHODS
    # ==========================================

    def create_webhook(self, board_id, url, event, config=None):
        """Registers a webhook on a Monday board."""
        self.ensure_one()
        query = """
        mutation ($board_id: ID!, $url: String!, $event: WebhookEventType!, $config: JSON) {
          create_webhook (board_id: $board_id, url: $url, event: $event, config: $config) {
            id
            board_id
          }
        }
        """
        # Event type must be mapped to Monday GraphQL Enum:
        # e.g., change_column_value, change_status_column_value, etc.
        _logger.info("Webhook Variables: %s", {
            "board_id": int(board_id),
            "url": url,
            "event": event,
            "config": config
        })


        res = self.execute_graphql(query, {
            "board_id": int(board_id),
            "url": url,
            "event": event,
            "config": json.dumps(config) if config else None
        })

        return res.get("create_webhook", {})


    # ==========================================
    # INTERNAL SYNCHRONIZATION HELPERS
    # ==========================================

    def _sync_project_to_board(self, project):
        """Syncs an Odoo Project to a Monday Board."""
        # Check mapping
        mapping = self.env['monday.record.mapping'].search([
            ('instance_id', '=', self.id),
            ('odoo_model', '=', 'project.project'),
            ('odoo_id', '=', project.id)
        ], limit=1)

        if not mapping:
            # Create board in Monday
            board_id = self.create_board(project.name)
            if board_id:
                # Store Board Mapping
                model_project_task = self.env['ir.model'].search([('model', '=', 'project.task')], limit=1)
                board_rec = self.env['monday.board'].create({
                    'name': project.name,
                    'instance_id': self.id,
                    'monday_board_id': board_id,
                    'odoo_project_id': project.id,
                    'odoo_model_id': model_project_task.id if model_project_task else False,
                })
                # Add Record Mapping
                self.env['monday.record.mapping'].create({
                    'instance_id': self.id,
                    'odoo_model': 'project.project',
                    'odoo_id': project.id,
                    'monday_board_id': board_rec.id,
                    'sync_status': 'synced',
                    'last_sync': fields.Datetime.now()
                })
                try:
                    # Auto import columns and auto map fields for tasks
                    board_rec.action_import_columns()
                    board_rec.action_auto_map_fields()
                except Exception as map_err:
                    _logger.warning("Failed to auto map columns/fields for new board: %s", str(map_err))
        else:
            # Maybe update board name if project name changed
            # Monday.com API doesn't support updating board names easily via mutation (read-only in some APIs).
            # We can log or skip name updates
            pass

    def _sync_milestone_to_group(self, project_milestone, payload):
        """Syncs Odoo Project Milestone to Monday Group."""
        project = project_milestone.project_id
        # Find Monday Board ID
        board_mapping = self.env['monday.record.mapping'].search([
            ('instance_id', '=', self.id),
            ('odoo_model', '=', 'project.project'),
            ('odoo_id', '=', project.id)
        ], limit=1)
        if not board_mapping or not board_mapping.monday_board_id:
            # Requeue or throw error
            raise UserError(_("Cannot sync milestone because parent project board is not mapped to Monday."))

        board_rec = board_mapping.monday_board_id
        m_board_id = board_rec.monday_board_id

        # Check group mapping
        group_mapping = self.env['monday.record.mapping'].search([
            ('instance_id', '=', self.id),
            ('odoo_model', '=', 'project.milestone'),
            ('odoo_id', '=', project_milestone.id)
        ], limit=1)

        if not group_mapping:
            # Create group in Monday
            m_group_id = self.create_group(m_board_id, project_milestone.name)
            if m_group_id:
                # Save group mapping
                group_rec = self.env['monday.group'].create({
                    'name': project_milestone.name,
                    'monday_group_id': m_group_id,
                    'board_id': board_rec.id,
                })
                self.env['monday.record.mapping'].create({
                    'instance_id': self.id,
                    'odoo_model': 'project.milestone',
                    'odoo_id': project_milestone.id,
                    'monday_board_id': board_rec.id,
                    'monday_group_id': group_rec.id,
                    'sync_status': 'synced',
                    'last_sync': fields.Datetime.now()
                })
        else:
            # Update group title
            m_group_id = group_mapping.monday_group_id.monday_group_id
            self.update_group(m_board_id, m_group_id, project_milestone.name)
            group_mapping.write({
                'sync_status': 'synced',
                'last_sync': fields.Datetime.now()
            })

    def _upload_file_to_item(self, record, attachment, column_id):
        """Uploads Odoo attachment file content to Monday column."""
        # Find record mapping
        mapping = self.env['monday.record.mapping'].search([
            ('instance_id', '=', self.id),
            ('odoo_model', '=', record._name),
            ('odoo_id', '=', record.id)
        ], limit=1)
        if not mapping or not mapping.monday_item_id:
            return

        query = """
        mutation ($file: File!, $item_id: ID!, $column_id: String!) {
          add_file_to_column (file: $file, item_id: $item_id, column_id: $column_id) {
            id
          }
        }
        """
        # Read file binary from attachment
        file_content = attachment.raw
        file_name = attachment.name

        client = self.connect()
        client.execute_multipart(
            query=query,
            file_name=file_name,
            file_content=file_content,
            variables={
                "item_id": int(mapping.monday_item_id),
                "column_id": column_id
            }
        )

    def _process_item_create(self, record):
        """Maps Odoo record and creates a new Monday item."""
        # 0. Check if record is already mapped to prevent duplicate exports
        existing_mapping = self.env['monday.record.mapping'].search([
            ('instance_id', '=', self.id),
            ('odoo_model', '=', record._name),
            ('odoo_id', '=', record.id),
            ('monday_item_id', '!=', False)
        ], limit=1)
        if existing_mapping and existing_mapping.monday_item_id:
            _logger.info("Record %s (%s) is already mapped to Monday item %s. Skipping duplicate create.", record._name, record.id, existing_mapping.monday_item_id)
            return

        # 1. Resolve board/group mapping
        board_id, group_id = self._resolve_board_and_group(record)
        if not board_id:
            # Board not found/configured, skip sync
            _logger.warning("Could not sync record %s (%s) because no mapped board was found.", record._name, record.id)
            return

        # 2. Get active mappings for this Odoo model and board
        mappings = self.env['monday.mapping'].search([
            ('instance_id', '=', self.id),
            ('odoo_model_name', '=', record._name),
            ('monday_board_id', '=', board_id.id),
            ('direction', 'in', ['odoo_to_monday', 'two_way'])
        ])

        column_values = {}
        item_name = record.display_name or "Odoo Record"

        # Special fallback case: if we are mapping project.task and has parent task (subtask)
        if record._name == 'project.task' and record.parent_id:
            parent_mapping = self.env['monday.record.mapping'].search([
                ('instance_id', '=', self.id),
                ('odoo_model', '=', 'project.task'),
                ('odoo_id', '=', record.parent_id.id),
                ('monday_item_id', '!=', False)
            ], limit=1)
            if parent_mapping and parent_mapping.monday_item_id:
                query = """
                mutation ($parent_item_id: ID!, $item_name: String!, $column_values: JSON) {
                  create_subitem (parent_item_id: $parent_item_id, item_name: $item_name, column_values: $column_values) {
                    id
                  }
                }
                """
                vars_payload = {
                    "parent_item_id": int(parent_mapping.monday_item_id),
                    "item_name": record.name
                }
                if column_values:
                    vars_payload["column_values"] = json.dumps(column_values)
                res = self.execute_graphql(query, vars_payload)
                m_subitem_id = res.get("create_subitem", {}).get("id")
                if m_subitem_id:
                    self.env['monday.record.mapping'].create({
                        'instance_id': self.id,
                        'odoo_model': record._name,
                        'odoo_id': record.id,
                        'monday_board_id': board_id.id,
                        'monday_item_id': m_subitem_id,
                        'sync_status': 'synced',
                        'last_sync': fields.Datetime.now()
                    })
                return

        for mapping_rule in mappings:
            col_val = self._convert_odoo_to_monday(record, mapping_rule)
            if col_val is not None:
                # Monday.com column_values expects {column_id: value}
                column_values[mapping_rule.monday_column_id] = col_val

        # Create Item
        m_item_id = self.create_item(board_id.monday_board_id, group_id, item_name, column_values)

        if m_item_id:
            # Save mapping
            self.env['monday.record.mapping'].create({
                'instance_id': self.id,
                'odoo_model': record._name,
                'odoo_id': record.id,
                'monday_board_id': board_id.id,
                'monday_item_id': m_item_id,
                'sync_status': 'synced',
                'last_sync': fields.Datetime.now()
            })

            # Handle attachments upload after item creation
            if hasattr(record, 'attachment_ids') and record.attachment_ids:
                # Find if we mapped a 'files' column
                file_mapping = mappings.filtered(lambda r: r.monday_column_type == 'files')
                if file_mapping:
                    for att in record.attachment_ids:
                        self.env['monday.queue.job'].enqueue(
                            instance=self,
                            model_name=record._name,
                            record_id=record.id,
                            operation='upload_file',
                            payload_dict={'attachment_id': att.id, 'monday_column_id': file_mapping[0].monday_column_id}
                        )

    def _process_item_update(self, record, changed_fields=None):
        """Maps updated Odoo fields and pushes updates to Monday.com."""
        mapping = self.env['monday.record.mapping'].search([
            ('instance_id', '=', self.id),
            ('odoo_model', '=', record._name),
            ('odoo_id', '=', record.id),
            ('monday_item_id', '!=', False)
        ], limit=1)

        if not mapping or not mapping.monday_item_id:
            # Does not exist on Monday, trigger a create instead
            self._process_item_create(record)
            return

        board_id = mapping.monday_board_id
        m_item_id = mapping.monday_item_id

        # Check if stage or milestone changed, and move the item to the new group
        if changed_fields and ('stage_id' in changed_fields or 'milestone_id' in changed_fields):
            _, new_group_id = self._resolve_board_and_group(record)
            if new_group_id:
                try:
                    self.move_item(board_id.monday_board_id, new_group_id, m_item_id)
                except Exception as me:
                    _logger.error("Failed to move Monday item %s to group %s: %s", m_item_id, new_group_id, str(me))

        # Get active mappings
        domain = [
            ('instance_id', '=', self.id),
            ('odoo_model_name', '=', record._name),
            ('monday_board_id', '=', board_id.id),
            ('direction', 'in', ['odoo_to_monday', 'two_way'])
        ]
        if changed_fields:
            domain.append(('odoo_field_name', 'in', changed_fields))

        mappings = self.env['monday.mapping'].search(domain)
        if mappings:
            column_values = {}
            for mapping_rule in mappings:
                col_val = self._convert_odoo_to_monday(record, mapping_rule)
                if col_val is not None:
                    column_values[mapping_rule.monday_column_id] = col_val

            if column_values:
                self.update_item(board_id.monday_board_id, m_item_id, column_values)

        mapping.write({
            'sync_status': 'synced',
            'last_sync': fields.Datetime.now()
        })

    def _resolve_board_and_group(self, record):
        """Resolves Monday Board and Group for the record."""
        board_id = None
        group_id = "topics"  # default group

        if record._name == 'res.partner':
            # Partner Board
            board_id = self.env['monday.board'].search([
                ('instance_id', '=', self.id),
                ('odoo_model_id.model', '=', 'res.partner')
            ], limit=1)
        elif record._name == 'project.project':
            # Handled separately by sync_project
            pass
        elif record._name == 'project.task':
            # Find Board mapped to the Task's Project
            board_mapping = self.env['monday.record.mapping'].search([
                ('instance_id', '=', self.id),
                ('odoo_model', '=', 'project.project'),
                ('odoo_id', '=', record.project_id.id)
            ], limit=1)
            if not board_mapping and record.project_id:
                # Dynamically sync project to Monday if not mapped yet
                self._sync_project_to_board(record.project_id)
                # Re-search board mapping
                board_mapping = self.env['monday.record.mapping'].search([
                    ('instance_id', '=', self.id),
                    ('odoo_model', '=', 'project.project'),
                    ('odoo_id', '=', record.project_id.id)
                ], limit=1)
            if board_mapping:
                board_id = board_mapping.monday_board_id

            # Find Group mapped to the task's milestone or stage
            if record.milestone_id:
                group_mapping = self.env['monday.record.mapping'].search([
                    ('instance_id', '=', self.id),
                    ('odoo_model', '=', 'project.milestone'),
                    ('odoo_id', '=', record.milestone_id.id)
                ], limit=1)
                if group_mapping and group_mapping.monday_group_id:
                    group_id = group_mapping.monday_group_id.monday_group_id

            # Fallback to stage mapping if group_id is still default or not resolved
            if (group_id == "topics" or not group_id) and record.stage_id:
                if record.stage_id.monday_group_id:
                    group_id = record.stage_id.monday_group_id
        elif record._name == 'sale.order':
            board_id = self.env['monday.board'].search([
                ('instance_id', '=', self.id),
                ('odoo_model_id.model', '=', 'sale.order')
            ], limit=1)
        elif record._name == 'helpdesk.ticket':
            board_id = self.env['monday.board'].search([
                ('instance_id', '=', self.id),
                ('odoo_model_id.model', '=', 'helpdesk.ticket')
            ], limit=1)

        return board_id, group_id

    def _convert_odoo_to_monday(self, record, mapping_rule):
        """Converts an Odoo field value to Monday column-values structure."""
        field_name = mapping_rule.odoo_field_name
        col_type = mapping_rule.monday_column_type

        # Use safe getattr
        raw_val = getattr(record, field_name, None)
        if raw_val is None:
            return None

        # Apply Transform Function if defined
        if mapping_rule.transform_function:
            try:
                # Evaluation context
                localdict = {'record': record, 'value': raw_val, 'result': None}
                # Safe execution or evaluation of snippet
                exec(mapping_rule.transform_function, {}, localdict)
                raw_val = localdict.get('result', raw_val)
            except Exception as te:
                _logger.error("Error executing transform function on rule %s: %s", mapping_rule.id, str(te))

        # Handle Relational Fields (Many2one, Many2many, One2many)
        is_relational = hasattr(raw_val, '_name')

        if col_type == 'text':
            if is_relational:
                return raw_val.display_name or ''
            return str(raw_val)

        elif col_type == 'numbers':
            if is_relational:
                return 0
            try:
                return float(raw_val)
            except (ValueError, TypeError):
                return 0

        elif col_type == 'date':
            if not raw_val:
                return None
            # Odoo date or datetime fields
            if isinstance(raw_val, (fields.Date, fields.Datetime)) or hasattr(raw_val, 'strftime'):
                return {"date": raw_val.strftime('%Y-%m-%d')}
            # string fallback
            return {"date": str(raw_val)[:10]}

        elif col_type == 'status':
            # Monday Status expects simple label or index
            if is_relational:
                return {"label": raw_val.display_name or ''}
            if isinstance(raw_val, str):
                return {"label": raw_val}
            return None

        elif col_type == 'people':
            # Resolve Odoo user/partner to Monday user ID
            email = None
            if is_relational:
                # If partner or user field
                if raw_val._name == 'res.users':
                    email = raw_val.login or raw_val.email
                elif raw_val._name == 'res.partner':
                    email = raw_val.email
            else:
                email = str(raw_val)

            m_user_id = self._get_monday_user_id_by_email(email)
            if m_user_id:
                return {"personsAndTeams": [{"id": int(m_user_id), "kind": "person"}]}
            return None

        elif col_type == 'email':
            email_val = raw_val.email if is_relational and raw_val._name in ['res.users', 'res.partner'] else str(raw_val)
            if email_val:
                return {"email": email_val, "text": email_val}
            return None

        elif col_type == 'phone':
            phone_val = raw_val.phone if is_relational and hasattr(raw_val, 'phone') else str(raw_val)
            if phone_val:
                return {"phone": phone_val, "countryShortName": "US"}
            return None

        elif col_type == 'dropdown':
            if is_relational:
                return {"labels": [raw_val.display_name]}
            return {"labels": [str(raw_val)]}

        elif col_type == 'checkbox':
            return {"checked": "true" if raw_val else "false"}

        elif col_type == 'long_text':
            return {"text": str(raw_val)}

        elif col_type == 'link':
            return {"url": str(raw_val), "text": "Odoo Link"}

        # Files, formulas, mirror are handled separately or read-only
        return None

    def _get_monday_user_id_by_email(self, email):
        """Lookup Monday User ID by email address."""
        if not email:
            return None
        # Simple caching using request/env context
        if not hasattr(self.env, '_monday_user_cache'):
            setattr(self.env, '_monday_user_cache', {})

        cache = getattr(self.env, '_monday_user_cache')
        if email in cache:
            return cache[email]

        try:
            query = """
            query ($email: String!) {
              users (emails: [$email]) {
                id
              }
            }
            """
            res = self.execute_graphql(query, {"email": email})
            users = res.get("users", [])
            if users:
                m_id = users[0]["id"]
                cache[email] = m_id
                return m_id
        except Exception as e:
            _logger.error("Failed to query Monday.com user by email %s: %s", email, str(e))

        return None

    @api.model
    def cron_sync_projects_and_milestones(self):
        """Finds modified projects and milestones and enqueues sync jobs."""
        instances = self.search([('active', '=', True)])
        for instance in instances:
            domain = []
            if instance.last_sync:
                domain.append(('write_date', '>', instance.last_sync))
            
            projects = self.env['project.project'].search(domain)
            for project in projects:
                self.env['monday.queue.job'].enqueue(instance, 'project.project', project.id, 'sync_project')

            milestones = self.env['project.milestone'].search(domain)
            for milestone in milestones:
                self.env['monday.queue.job'].enqueue(instance, 'project.milestone', milestone.id, 'sync_milestone')

    @api.model
    def cron_sync_tasks(self):
        """Finds modified project tasks and enqueues sync jobs."""
        instances = self.search([('active', '=', True)])
        for instance in instances:
            domain = []
            if instance.last_sync:
                domain.append(('write_date', '>', instance.last_sync))
            tasks = self.env['project.task'].search(domain)
            for task in tasks:
                mapping = self.env['monday.record.mapping'].search([
                    ('instance_id', '=', instance.id),
                    ('odoo_model', '=', 'project.task'),
                    ('odoo_id', '=', task.id)
                ], limit=1)
                op = 'update' if mapping else 'create'
                self.env['monday.queue.job'].enqueue(instance, 'project.task', task.id, op)

            # Update last_sync timestamp
            instance.write({'last_sync': fields.Datetime.now()})

    def action_import_data(self):
        """Orchestrates direct import processes based on configured settings."""
        self.ensure_one()
        self = self.with_context(monday_sync_direction='import')
        summary = []
        if self.import_users:
            res = self._import_users()
            summary.append(_("Users (Created: %s, Updated: %s)") % (res.get('created', 0), res.get('updated', 0)))

        if self.import_boards:
            res = self._import_boards()
            summary.append(_("Boards (Created: %s, Updated: %s)") % (res.get('created', 0), res.get('updated', 0)))

        if self.import_groups:
            res = self._import_groups()
            summary.append(_("Groups (Created: %s, Updated: %s)") % (res.get('created', 0), res.get('updated', 0)))

        if self.import_items:
            res = self._import_items()
            summary.append(_("Items (Created: %s, Updated: %s)") % (res.get('created', 0), res.get('updated', 0)))

        message = _("Import completed: ") + (", ".join(summary) if summary else _("No options selected"))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Import Data'),
                'message': message,
                'sticky': True,
                'type': 'success',
            }
        }

    def _import_users(self):
        """Imports users from Monday.com as res.partner records."""
        self.ensure_one()
        query = """
        query {
          users {
            id
            name
            email
          }
        }
        """
        try:
            res = self.execute_graphql(query)
            users = res.get("users", [])
            created = 0
            updated = 0
            for u in users:
                email = u.get("email")
                name = u.get("name")
                if not email:
                    continue

                partner = self.env['res.partner'].search([('email', '=', email)], limit=1)
                if not partner:
                    self.env['res.partner'].create({
                        'name': name,
                        'email': email,
                        'comment': _('Imported from Monday.com User ID: %s') % u.get('id')
                    })
                    created += 1
                else:
                    partner.write({
                        'name': name,
                    })
                    updated += 1
            self.env['monday.sync.entry'].log_sync_entry(
                instance=self,
                operation='Create',
                import_action='Users',
                action_done='Import',
                status='success'
            )
            return {'created': created, 'updated': updated}
        except Exception as e:
            self.env['monday.sync.entry'].log_sync_entry(
                instance=self,
                operation='Create',
                import_action='Users',
                action_done='Import',
                status='failed'
            )
            _logger.error("Failed to import Monday users: %s", str(e))
            raise UserError(_("Failed to import users: %s") % str(e))

    def _import_boards(self):
        """Imports boards from Monday.com and automatically maps them to Odoo projects."""
        self.ensure_one()
        query = """
        query {
          boards {
            id
            name
            board_kind
            type
          }
        }
        """
        try:
            res = self.execute_graphql(query)
            boards = res.get("boards", [])
            created = 0
            updated = 0
            model_project_task = self.env['ir.model'].search([('model', '=', 'project.task')], limit=1)
            for b in boards:
                # Filter out non-board types (like documents or sub-item boards)
                if b.get('type') and b.get('type') != 'board':
                    continue

                b_id = str(b["id"])
                b_name = b["name"]
                b_kind = b["board_kind"]
                b_type = 'shareable' if b_kind == 'share' else b_kind

                existing = self.env['monday.board'].search([
                    ('instance_id', '=', self.id),
                    ('monday_board_id', '=', b_id)
                ], limit=1)

                board_vals = {
                    'name': b_name,
                    'board_type': b_type,
                }

                # Auto-create Odoo project if not already linked
                project_id = False
                if existing and existing.odoo_project_id:
                    project_id = existing.odoo_project_id.id
                else:
                    # Find or create project
                    project = self.env['project.project'].search([('name', '=', b_name)], limit=1)
                    if project:
                        # Check if this project is already mapped to a different board
                        mapped_board = self.env['monday.board'].search([
                            ('instance_id', '=', self.id),
                            ('odoo_project_id', '=', project.id),
                            ('monday_board_id', '!=', b_id)
                        ], limit=1)
                        if mapped_board:
                            project = False

                    if not project:
                        project = self.env['project.project'].create({'name': b_name})
                    project_id = project.id
                    board_vals.update({
                        'odoo_project_id': project_id,
                        'odoo_model_id': model_project_task.id if model_project_task else False
                    })

                if existing:
                    existing.write(board_vals)
                    board_rec = existing
                    updated += 1
                else:
                    board_vals.update({
                        'instance_id': self.id,
                        'monday_board_id': b_id,
                    })
                    board_rec = self.env['monday.board'].create(board_vals)
                    created += 1

                # Ensure record mapping for the project exists
                if project_id:
                    rec_mapping = self.env['monday.record.mapping'].search([
                        ('instance_id', '=', self.id),
                        ('odoo_model', '=', 'project.project'),
                        ('odoo_id', '=', project_id)
                    ], limit=1)
                    if not rec_mapping:
                        self.env['monday.record.mapping'].create({
                            'instance_id': self.id,
                            'odoo_model': 'project.project',
                            'odoo_id': project_id,
                            'monday_board_id': board_rec.id,
                            'sync_status': 'synced',
                            'last_sync': fields.Datetime.now()
                        })

            self.env['monday.sync.entry'].log_sync_entry(
                instance=self,
                operation='Create',
                import_action='Boards',
                action_done='Import',
                status='success'
            )
            return {'created': created, 'updated': updated}
        except Exception as e:
            self.env['monday.sync.entry'].log_sync_entry(
                instance=self,
                operation='Create',
                import_action='Boards',
                action_done='Import',
                status='failed'
            )
            _logger.error("Failed to import Monday boards: %s", str(e))
            raise UserError(_("Failed to import boards: %s") % str(e))

    def _import_groups(self):
        """Imports groups for all boards under this instance and maps them to Odoo task stages."""
        self.ensure_one()
        try:
            boards = self.env['monday.board'].search([('instance_id', '=', self.id)])
            created = 0
            updated = 0
            for board in boards:
                try:
                    groups = self.get_groups(board.monday_board_id)
                    existing_groups = {g.monday_group_id: g for g in board.group_ids}
                    for grp in groups:
                        g_id = grp["id"]
                        g_title = grp["title"]
                        if g_id in existing_groups:
                            existing_groups[g_id].write({'name': g_title})
                            updated += 1
                        else:
                            self.env['monday.group'].create({
                                'board_id': board.id,
                                'name': g_title,
                                'monday_group_id': g_id
                            })
                            created += 1

                        # Auto-create Odoo Task Stage if board is mapped to a project
                        if board.odoo_project_id:
                            stage = self.env['project.task.type'].search([
                                ('name', '=', g_title),
                                ('project_ids', 'in', [board.odoo_project_id.id])
                            ], limit=1)
                            if not stage:
                                stage = self.env['project.task.type'].search([
                                    ('monday_group_id', '=', g_id)
                                ], limit=1)
                                if not stage:
                                    stage = self.env['project.task.type'].create({
                                        'name': g_title,
                                        'monday_instance_id': self.id,
                                        'monday_group_id': g_id,
                                        'project_ids': [(4, board.odoo_project_id.id)]
                                    })
                                else:
                                    stage.write({
                                        'name': g_title,
                                        'monday_instance_id': self.id,
                                        'project_ids': [(4, board.odoo_project_id.id)]
                                    })
                            else:
                                stage.write({
                                    'monday_instance_id': self.id,
                                    'monday_group_id': g_id
                                })
                except Exception as e:
                    _logger.error("Failed to import groups for board %s: %s", board.name, str(e))
            self.env['monday.sync.entry'].log_sync_entry(
                instance=self,
                operation='Create',
                import_action='Groups',
                action_done='Import',
                status='success'
            )
            return {'created': created, 'updated': updated}
        except Exception as e:
            self.env['monday.sync.entry'].log_sync_entry(
                instance=self,
                operation='Create',
                import_action='Groups',
                action_done='Import',
                status='failed'
            )
            raise e

    def _import_items(self):
        """Imports items for all active/mapped boards under this instance."""
        self.ensure_one()
        try:
            boards = self.env['monday.board'].search([
                ('instance_id', '=', self.id),
                ('active', '=', True)
            ])
            created = 0
            updated = 0
            for board in boards:
                if board.odoo_model_id:
                    res = self._import_items_for_board(board)
                    created += res.get('created', 0)
                    updated += res.get('updated', 0)
            self.env['monday.sync.entry'].log_sync_entry(
                instance=self,
                operation='Create',
                import_action='Items',
                action_done='Import',
                status='success'
            )
            return {'created': created, 'updated': updated}
        except Exception as e:
            self.env['monday.sync.entry'].log_sync_entry(
                instance=self,
                operation='Create',
                import_action='Items',
                action_done='Import',
                status='failed'
            )
            raise e

    def _import_items_for_board(self, board):
        """Helper to import items of a specific board."""
        self.ensure_one()
        if not board.odoo_model_id:
            return 0
        model_name = board.odoo_model_id.model
        if model_name not in self.env or self.env[model_name]._abstract or self.env[model_name]._transient:
            return 0

        # Get column mappings for this board
        mappings = self.env['monday.mapping'].search([
            ('instance_id', '=', self.id),
            ('monday_board_id', '=', board.id),
            ('direction', 'in', ['monday_to_odoo', 'two_way'])
        ])
        if not mappings:
            # Auto-map columns if not configured yet
            board.action_auto_map_fields()
            mappings = self.env['monday.mapping'].search([
                ('instance_id', '=', self.id),
                ('monday_board_id', '=', board.id),
                ('direction', 'in', ['monday_to_odoo', 'two_way'])
            ])

        query = """
        query ($board_id: [ID!], $cursor: String) {
          boards (ids: $board_id) {
            items_page (limit: 100, cursor: $cursor) {
              cursor
              items {
                id
                name
                group {
                  id
                }
                column_values {
                  id
                  text
                  value
                  type
                }
              }
            }
          }
        }
        """

        created = 0
        updated = 0
        cursor = None
        has_more = True
        ctx = {'from_monday_webhook': True}
        env = self.env(context=ctx)

        while has_more:
            variables = {
                "board_id": [int(board.monday_board_id)],
                "cursor": cursor
            }
            res = self.execute_graphql(query, variables)
            boards_data = res.get("boards", [])
            if not boards_data:
                break

            items_page = boards_data[0].get("items_page", {})
            items = items_page.get("items", [])
            cursor = items_page.get("cursor")

            for item in items:
                item_id = str(item["id"])
                item_name = item["name"]
                group_id = item.get("group", {}).get("id") if item.get("group") else None

                mapping = env['monday.record.mapping'].search([
                    ('instance_id', '=', self.id),
                    ('monday_item_id', '=', item_id)
                ], limit=1)

                vals = {'name': item_name}

                if model_name == 'project.task' and board.odoo_project_id:
                    vals['project_id'] = board.odoo_project_id.id

                # Resolve group to Odoo milestone or task stage
                if model_name == 'project.task' and group_id:
                    grp_mapping = env['monday.record.mapping'].search([
                        ('instance_id', '=', self.id),
                        ('odoo_model', '=', 'project.milestone'),
                        ('monday_group_id.monday_group_id', '=', group_id)
                    ], limit=1)
                    if grp_mapping:
                        vals['milestone_id'] = grp_mapping.odoo_id

                    stage = env['project.task.type'].search([
                        ('monday_group_id', '=', group_id)
                    ], limit=1)
                    if stage:
                        vals['stage_id'] = stage.id

                col_vals = {c['id']: c for c in item.get('column_values', [])}

                for rule in mappings:
                    col_data = col_vals.get(rule.monday_column_id)
                    if col_data:
                        val = self._convert_monday_to_odoo(env, rule, col_data)
                        if val is not None:
                            vals[rule.odoo_field_name] = val

                if mapping:
                    record = env[model_name].browse(mapping.odoo_id)
                    if record.exists():
                        record.write(vals)
                        mapping.write({
                            'sync_status': 'synced',
                            'last_sync': fields.Datetime.now()
                        })
                        updated += 1
                    else:
                        new_record = env[model_name].create(vals)
                        mapping.write({
                            'odoo_id': new_record.id,
                            'sync_status': 'synced',
                            'last_sync': fields.Datetime.now()
                        })
                        created += 1
                else:
                    new_record = env[model_name].create(vals)
                    env['monday.record.mapping'].create({
                        'instance_id': self.id,
                        'odoo_model': model_name,
                        'odoo_id': new_record.id,
                        'monday_board_id': board.id,
                        'monday_item_id': item_id,
                        'sync_status': 'synced',
                        'last_sync': fields.Datetime.now()
                    })
                    created += 1

            if not cursor:
                has_more = False

        return {'created': created, 'updated': updated}

    def _convert_monday_to_odoo(self, env, rule, col_data):
        """Converts Monday column data to Odoo field type."""
        col_type = rule.monday_column_type
        val_str = col_data.get('value')
        text_val = col_data.get('text', '')

        if not val_str and not text_val:
            return None

        details = {}
        if val_str:
            try:
                details = json.loads(val_str)
            except Exception:
                pass

        field = rule.odoo_field_id
        ttype = field.ttype

        if rule.transform_function:
            try:
                localdict = {'value': text_val, 'details': details, 'result': None}
                exec(rule.transform_function, {}, localdict)
                return localdict.get('result')
            except Exception:
                pass

        if col_type == 'checkbox':
            return details.get('checked') == 'true' or text_val == 'v' or details.get('checked') is True

        elif col_type == 'numbers':
            try:
                num = float(text_val) if text_val else 0.0
                if ttype == 'integer':
                    return int(num)
                return num
            except ValueError:
                return 0

        elif col_type == 'date':
            date_str = details.get('date') or text_val[:10]
            if date_str:
                return date_str
            return None

        elif col_type == 'people':
            persons = details.get('personsAndTeams', [])
            if persons:
                p_id = persons[0].get('id')
                query = """
                query ($ids: [ID!]) {
                  users (ids: $ids) {
                    email
                  }
                }
                """
                res = self.execute_graphql(query, {"ids": [int(p_id)]})
                users = res.get("users", [])
                if users:
                    email = users[0].get("email")
                    if ttype == 'many2one':
                        relation = field.relation
                        if relation == 'res.users':
                            user = env['res.users'].search([('login', '=', email)], limit=1)
                            if user:
                                return user.id
                        elif relation == 'res.partner':
                            partner = env['res.partner'].search([('email', '=', email)], limit=1)
                            if partner:
                                return partner.id
            return None

        if ttype == 'char' or ttype == 'text':
            return text_val
        elif ttype == 'many2one':
            relation = field.relation
            rec = env[relation].search([('name', '=', text_val)], limit=1)
            return rec.id if rec else None

        return None

    def _process_single_import(self, entity_type, board_id, line_data):
        """Processes a single record import."""
        self.ensure_one()
        action_type = line_data.get('action_type')
        if action_type == 'skip':
            return 'skip'

        m_id = line_data.get('monday_id')
        name = line_data.get('name')
        metadata = json.loads(line_data.get('metadata') or '{}')

        if entity_type == 'boards':
            b_kind = metadata.get("board_kind", "public")
            b_type = 'shareable' if b_kind == 'share' else b_kind
            existing = self.env['monday.board'].search([
                ('instance_id', '=', self.id),
                ('monday_board_id', '=', m_id)
            ], limit=1)

            board_vals = {
                'name': name,
                'board_type': b_type,
            }

            model_project_task = self.env['ir.model'].search([('model', '=', 'project.task')], limit=1)
            project_id = False
            if existing and existing.odoo_project_id:
                project_id = existing.odoo_project_id.id
            else:
                project = self.env['project.project'].search([('name', '=', name)], limit=1)
                if project:
                    mapped_board = self.env['monday.board'].search([
                        ('instance_id', '=', self.id),
                        ('odoo_project_id', '=', project.id),
                        ('monday_board_id', '!=', m_id)
                    ], limit=1)
                    if mapped_board:
                        project = False
                if not project:
                    project = self.env['project.project'].create({'name': name})
                project_id = project.id
                board_vals.update({
                    'odoo_project_id': project_id,
                    'odoo_model_id': model_project_task.id if model_project_task else False
                })

            if existing:
                existing.write(board_vals)
                board_rec = existing
            else:
                board_vals.update({
                    'instance_id': self.id,
                    'monday_board_id': m_id,
                })
                board_rec = self.env['monday.board'].create(board_vals)

            if project_id:
                rec_mapping = self.env['monday.record.mapping'].search([
                    ('instance_id', '=', self.id),
                    ('odoo_model', '=', 'project.project'),
                    ('odoo_id', '=', project_id)
                ], limit=1)
                if not rec_mapping:
                    self.env['monday.record.mapping'].create({
                        'instance_id': self.id,
                        'odoo_model': 'project.project',
                        'odoo_id': project_id,
                        'monday_board_id': board_rec.id,
                        'sync_status': 'synced',
                        'last_sync': fields.Datetime.now()
                    })
            return action_type

        elif entity_type == 'groups':
            board = self.env['monday.board'].browse(board_id)
            existing = self.env['monday.group'].search([
                ('board_id', '=', board.id),
                ('monday_group_id', '=', m_id)
            ], limit=1)
            if existing:
                existing.write({'name': name})
            else:
                self.env['monday.group'].create({
                    'board_id': board.id,
                    'name': name,
                    'monday_group_id': m_id
                })
            return action_type

        elif entity_type == 'users':
            email = metadata.get("email")
            if not email:
                return 'skip'
            partner = self.env['res.partner'].search([('email', '=', email)], limit=1)
            if partner:
                partner.write({
                    'name': name,
                    'comment': _('Updated from Monday.com User ID: %s') % m_id
                })
            else:
                self.env['res.partner'].create({
                    'name': name,
                    'email': email,
                    'comment': _('Imported from Monday.com User ID: %s') % m_id
                })
            return action_type

        elif entity_type == 'items':
            board = self.env['monday.board'].browse(board_id)
            model_name = board.odoo_model_id.model
            
            # Find column mappings
            mappings = self.env['monday.mapping'].search([
                ('instance_id', '=', self.id),
                ('monday_board_id', '=', board.id),
                ('direction', 'in', ['monday_to_odoo', 'two_way'])
            ])
            if not mappings:
                board.action_auto_map_fields()
                mappings = self.env['monday.mapping'].search([
                    ('instance_id', '=', self.id),
                    ('monday_board_id', '=', board.id),
                    ('direction', 'in', ['monday_to_odoo', 'two_way'])
                ])

            ctx = {'from_monday_webhook': True}
            env = self.env(context=ctx)

            mapping = env['monday.record.mapping'].search([
                ('instance_id', '=', self.id),
                ('monday_board_id', '=', board.id),
                ('monday_item_id', '=', m_id)
            ], limit=1)

            vals = {'name': name}
            group_id = metadata.get("group", {}).get("id") if metadata.get("group") else None

            if model_name == 'project.task':
                vals['project_id'] = board.odoo_project_id.id if board.odoo_project_id else False
                if group_id:
                    grp_mapping = env['monday.group'].search([
                        ('board_id', '=', board.id),
                        ('monday_group_id', '=', group_id)
                    ], limit=1)
                    if grp_mapping:
                        stage = env['project.task.type'].search([
                            ('monday_group_id', '=', group_id),
                            ('project_ids', 'in', [board.odoo_project_id.id])
                        ], limit=1)
                        if stage:
                            vals['stage_id'] = stage.id

            # Map column values
            col_vals = {cv['id']: cv for cv in metadata.get('column_values', [])}
            import_wizard_obj = self.env['monday.import.wizard']
            for rule in mappings:
                col_id = rule.monday_column_id
                if col_id in col_vals:
                    val = import_wizard_obj._convert_monday_to_odoo(env, rule, col_vals[col_id])
                    if val is not None:
                        vals[rule.odoo_field_name] = val

            if mapping:
                record = env[model_name].browse(mapping.odoo_id)
                if record.exists():
                    record.write(vals)
                    mapping.write({
                        'sync_status': 'synced',
                        'last_sync': fields.Datetime.now()
                    })
            else:
                new_record = env[model_name].create(vals)
                env['monday.record.mapping'].create({
                    'instance_id': self.id,
                    'odoo_model': model_name,
                    'odoo_id': new_record.id,
                    'monday_board_id': board.id,
                    'monday_item_id': m_id,
                    'sync_status': 'synced',
                    'last_sync': fields.Datetime.now()
                })
            return action_type

    def _process_background_import(self, payload):
        """Processes background imports enqueued by the wizard."""
        entity_type = payload.get('entity_type')
        board_id = payload.get('board_id')
        lines = payload.get('lines', [])

        created = 0
        updated = 0
        skipped = 0

        for line in lines:
            try:
                res = self._process_single_import(entity_type, board_id, line)
                if res == 'create':
                    created += 1
                elif res == 'update':
                    updated += 1
                else:
                    skipped += 1
            except Exception as e:
                _logger.error("Background import failed for line %s: %s", line.get('monday_id'), str(e))
                skipped += 1

        self.env['monday.sync.entry'].log_sync_entry(
            instance=self,
            operation='Create',
            import_action=entity_type.title(),
            action_done='Import',
            status='success'
        )
        return True

