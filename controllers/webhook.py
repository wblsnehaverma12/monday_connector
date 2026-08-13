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

import hmac
import hashlib
import json
import logging
import traceback
from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)

_logger.warning("========== MONDAY WEBHOOK CONTROLLER LOADED ==========")

class MondayWebhookController(http.Controller):
    """Webhook controller to handle live incoming events from Monday.com."""

    @http.route('/monday/webhook/<int:instance_id>', type='http', auth='public', csrf=False)
    def receive_webhook(self, instance_id, **kwargs):
        _logger.warning("WEBHOOK METHOD ENTERED")
        """Processes live event updates from Monday.com."""
        instance = request.env['monday.instance'].sudo().browse(instance_id)
        if not instance.exists() or not instance.active:
            _logger.error("Received Monday.com webhook for inactive or invalid instance ID: %s", instance_id)
            return request.make_response(
                json.dumps({"status": "error", "message": "Instance not active"}),
                headers=[('Content-Type', 'application/json')]
            )

        # Read raw request data
        body = request.httprequest.data
        payload = json.loads(body.decode('utf-8') or '{}')

        # 1. Challenge verification (critical for Monday webhook handshakes)
        if 'challenge' in payload:
            _logger.info("Monday.com Webhook challenge handshake received for Instance %s", instance.name)
            return request.make_response(
                json.dumps({"challenge": payload['challenge']}),
                headers=[('Content-Type', 'application/json')]
            )

        # 2. Verify signature if webhook secret is configured
        if instance.webhook_secret:
            signature = request.httprequest.headers.get("Authorization")
            if not signature:
                _logger.warning("Missing Authorization signature header in Monday webhook")
                return request.make_response(
                    json.dumps({"status": "error", "message": "Unauthorized"}),
                    headers=[('Content-Type', 'application/json')]
                )

            calculated = hmac.new(
                instance.webhook_secret.encode('utf-8'),
                body,
                hashlib.sha256
            ).hexdigest()

            if not hmac.compare_digest(calculated, signature):
                _logger.warning("Invalid webhook signature from Monday.com for Instance %s", instance.name)
                return request.make_response(
                    json.dumps({"status": "error", "message": "Signature verification failed"}),
                    headers=[('Content-Type', 'application/json')]
                )

        # 3. Process webhook events
        try:
            event = payload.get('event', {})
            event_type = event.get('type')
            _logger.info("Processing Monday.com webhook event '%s' for Instance %s", event_type, instance.name)

            # Log the request
            request.env['monday.sync.log'].sudo().create({
                'name': f"Webhook Event: {event_type}",
                'instance_id': instance.id,
                'direction': 'webhook',
                'operation': event_type,
                'request_data': json.dumps(payload),
                'response_data': json.dumps({"status": "received"}),
                'duration': 0.0,
                'status': 'success'
            })

            # Call internal webhook handler
            self._handle_webhook_event(instance.with_context(monday_sync_direction='webhook'), event_type, event)

            return request.make_response(
                json.dumps({"status": "success"}),
                headers=[('Content-Type', 'application/json')]
            )

        except Exception as e:
            _logger.exception("Error processing Monday.com webhook:")
            # Log failure
            request.env['monday.sync.log'].sudo().create({
                'name': f"Webhook Error: {payload.get('event', {}).get('type', 'unknown')}",
                'instance_id': instance.id,
                'direction': 'webhook',
                'operation': 'webhook_error',
                'request_data': json.dumps(payload),
                'response_data': '',
                'duration': 0.0,
                'status': 'failed',
                'error_message': str(e),
                'traceback': traceback.format_exc()
            })
            return request.make_response(
                json.dumps({"status": "error", "message": str(e)}),
                headers=[('Content-Type', 'application/json')]
            )

    @http.route(
        '/monday/test',
        type='http',
        auth='public',
        csrf=False,
    )
    def monday_test(self, **kw):
        _logger.warning("TEST ROUTE HIT")
        return "OK"

    def _handle_webhook_event(self, instance, event_type, event):
        """Dispatches event types to correct sync methods."""
        # Prevent infinite loop back-syncs
        ctx = {'from_monday_webhook': True}
        env = request.env(context=ctx)

        # Pulse ID is standard key for Item ID in Monday.com webhooks
        item_id = str(event.get('pulseId') or event.get('itemId'))
        board_id = str(event.get('boardId'))

        if not item_id or not board_id:
            return

        # Find Monday board mapping
        m_board = env['monday.board'].sudo().search([
            ('instance_id', '=', instance.id),
            ('monday_board_id', '=', board_id)
        ], limit=1)

        if not m_board or not m_board.odoo_model_id:
            _logger.warning("No Odoo model mapped to Monday Board ID: %s", board_id)
            return

        model_name = m_board.odoo_model_id.model

        # 1. Event: Create Item
        if event_type == 'create_pulse' or event_type == 'create_item':
            # Check if mapping already exists
            existing_mapping = env['monday.record.mapping'].sudo().search([
                ('instance_id', '=', instance.id),
                ('monday_item_id', '=', item_id)
            ], limit=1)

            if existing_mapping:
                return

            # Read full item details from Monday.com to get column values
            item_data = instance.read_item(item_id)
            if not item_data:
                return

            # Create target Odoo record
            vals = {'name': item_data.get('name', 'Monday Item')}
            
            # Map columns
            mappings = env['monday.mapping'].sudo().search([
                ('instance_id', '=', instance.id),
                ('monday_board_id', '=', m_board.id),
                ('direction', 'in', ['monday_to_odoo', 'two_way'])
            ])

            # Build value mapping dict
            col_vals = {c['id']: c for c in item_data.get('column_values', [])}
            for rule in mappings:
                col_data = col_vals.get(rule.monday_column_id)
                if col_data:
                    val = self._convert_monday_to_odoo(env, rule, col_data)
                    if val is not None:
                        vals[rule.odoo_field_name] = val

            # Context override: ensure project_id is set if task
            if model_name == 'project.task' and m_board.odoo_project_id:
                vals['project_id'] = m_board.odoo_project_id.id

            # Create the record in Odoo
            new_record = env[model_name].sudo().create(vals)

            # Save record mapping
            env['monday.record.mapping'].sudo().create({
                'instance_id': instance.id,
                'odoo_model': model_name,
                'odoo_id': new_record.id,
                'monday_board_id': m_board.id,
                'monday_item_id': item_id,
                'sync_status': 'synced',
                'last_sync': fields.Datetime.now()
            })

        # 2. Event: Update Item / Column Changed
        elif event_type == 'change_column_value' or event_type == 'change_subitem_column_value':
            column_id = event.get('columnId')
            column_val = event.get('value')  # contains JSON details of value

            # Find record mapping
            rec_mapping = env['monday.record.mapping'].sudo().search([
                ('instance_id', '=', instance.id),
                ('monday_item_id', '=', item_id)
            ], limit=1)

            if not rec_mapping:
                # If not mapped, maybe it's a new item we missed
                return

            # Find mapping rule
            rule = env['monday.mapping'].sudo().search([
                ('instance_id', '=', instance.id),
                ('monday_board_id', '=', m_board.id),
                ('monday_column_id', '=', column_id),
                ('direction', 'in', ['monday_to_odoo', 'two_way'])
            ], limit=1)

            if not rule:
                return

            # Get target Odoo record
            odoo_record = env[model_name].sudo().browse(rec_mapping.odoo_id)
            if not odoo_record.exists():
                return

            # Convert column value
            val = self._convert_monday_to_odoo(env, rule, {'value': json.dumps(column_val)})
            if val is not None:
                odoo_record.sudo().write({rule.odoo_field_name: val})
                rec_mapping.write({
                    'sync_status': 'synced',
                    'last_sync': fields.Datetime.now()
                })

        # 3. Event: Deleted / Archived
        elif event_type == 'delete_pulse' or event_type == 'archive_pulse':
            rec_mapping = env['monday.record.mapping'].sudo().search([
                ('instance_id', '=', instance.id),
                ('monday_item_id', '=', item_id)
            ], limit=1)

            if rec_mapping:
                odoo_record = env[model_name].sudo().browse(rec_mapping.odoo_id)
                if odoo_record.exists():
                    if hasattr(odoo_record, 'active'):
                        odoo_record.sudo().write({'active': False})
                    else:
                        odoo_record.sudo().unlink()
                rec_mapping.sudo().unlink()

        # 4. Event: Item Moved to Group
        elif event_type in ['item_moved_to_any_group', 'item_moved_to_group']:
            group_id = event.get('afterGroupId') or event.get('groupId')
            if not group_id:
                return

            rec_mapping = env['monday.record.mapping'].sudo().search([
                ('instance_id', '=', instance.id),
                ('monday_item_id', '=', item_id)
            ], limit=1)

            if not rec_mapping:
                return

            odoo_record = env[model_name].sudo().browse(rec_mapping.odoo_id)
            if not odoo_record.exists():
                return

            if model_name == 'project.task':
                stage = env['project.task.type'].sudo().search([
                    ('monday_group_id', '=', group_id)
                ], limit=1)
                if stage:
                    odoo_record.sudo().write({'stage_id': stage.id})
                else:
                    # Or check milestone
                    grp_mapping = env['monday.record.mapping'].sudo().search([
                        ('instance_id', '=', instance.id),
                        ('odoo_model', '=', 'project.milestone'),
                        ('monday_group_id.monday_group_id', '=', group_id)
                    ], limit=1)
                    if grp_mapping:
                        odoo_record.sudo().write({'milestone_id': grp_mapping.odoo_id})


            rec_mapping.write({
                'sync_status': 'synced',
                'last_sync': fields.Datetime.now()
            })

    def _convert_monday_to_odoo(self, env, rule, col_data):
        """Converts Monday.com column structure to Odoo field type."""
        col_type = rule.monday_column_type
        # Monday.com column data generally has 'text' (human readable) and 'value' (JSON string of details)
        val_str = col_data.get('value')
        text_val = col_data.get('text', '')

        if not val_str and not text_val:
            return None

        # Parse JSON value if present
        details = {}
        if val_str:
            try:
                details = json.loads(val_str)
            except Exception:
                pass

        # Target Field type
        field = rule.odoo_field_id
        ttype = field.ttype

        # Handle transformations if defined
        if rule.transform_function:
            try:
                localdict = {'value': text_val, 'details': details, 'result': None}
                exec(rule.transform_function, {}, localdict)
                return localdict.get('result')
            except Exception as te:
                _logger.error("Transform function failed on webhook: %s", str(te))

        if col_type == 'checkbox':
            # Checkbox details usually contain {"checked": "true"}
            checked = details.get('checked') == 'true' or text_val == 'v' or details.get('checked') is True
            return checked

        elif col_type == 'numbers':
            # Extract number
            try:
                num = float(text_val) if text_val else 0.0
                if ttype == 'integer':
                    return int(num)
                return num
            except ValueError:
                return 0

        elif col_type == 'date':
            # Date format is usually YYYY-MM-DD
            # details contain {"date": "YYYY-MM-DD"}
            date_str = details.get('date') or text_val[:10]
            if date_str:
                return date_str
            return None

        elif col_type == 'people':
            # People details contain {"personsAndTeams": [{"id": 123, "kind": "person"}]}
            # We must resolve this by retrieving the user's email from Monday
            # Since we only have user ID, we can query the instance
            persons = details.get('personsAndTeams', [])
            if persons:
                p_id = persons[0].get('id')
                # Query Monday for email
                query = """
                query ($ids: [ID!]) {
                  users (ids: $ids) {
                    email
                  }
                }
                """
                res = rule.instance_id.execute_graphql(query, {"ids": [int(p_id)]})
                users = res.get("users", [])
                if users:
                    email = users[0].get("email")
                    # Match user in Odoo
                    if ttype == 'many2one':
                        # Find partner or user
                        relation = field.relation
                        if relation == 'res.users':
                            user = env['res.users'].sudo().search([('login', '=', email)], limit=1)
                            if user:
                                return user.id
                        elif relation == 'res.partner':
                            partner = env['res.partner'].sudo().search([('email', '=', email)], limit=1)
                            if partner:
                                return partner.id
            return None

        # Fallback to Text mapping
        if ttype == 'char' or ttype == 'text':
            return text_val
        elif ttype == 'many2one':
            # Try to match by display name
            relation = field.relation
            rec = env[relation].sudo().search([('name', '=', text_val)], limit=1)
            return rec.id if rec else None

        return None
