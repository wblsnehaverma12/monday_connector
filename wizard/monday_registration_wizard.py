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

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from ..utils.graphql_client import GraphQLClient


class MondayRegistrationWizard(models.TransientModel):
    """Wizard to register and establish connection to Monday.com."""
    _name = 'monday.registration.wizard'
    _description = 'Monday Registration Wizard'

    name = fields.Char(string='Account Name', required=True, default='Monday Account')
    api_token = fields.Char(string='API Token', required=True)

    def action_establish_connection(self):
        """Verifies connection and registers the Monday account."""
        self.ensure_one()
        client = GraphQLClient(
            api_token=self.api_token,
            api_version="2024-01",
            base_url="https://api.monday.com/v2"
        )

        try:
            # Query me and account to verify token
            query = """
            query {
              me {
                id
                name
                email
              }
              account {
                id
                name
              }
            }
            """
            res = client.execute(query)
            if not res or not res.get('me') or not res.get('account'):
                raise UserError(_("Connection test failed. Invalid API response."))

            account_id = str(res['account']['id'])

            # Create or update monday.instance
            instance_obj = self.env['monday.instance']
            existing = instance_obj.search([
                ('name', '=', self.name)
            ], limit=1)

            vals = {
                'name': self.name,
                'api_token': self.api_token,
                'status': 'connected',
                'active': True,
                'monday_account_id': account_id,
                'import_users': False,
                'import_boards': False,
                'import_groups': False,
            }

            if existing:
                existing.write(vals)
                instance = existing
            else:
                instance = instance_obj.create(vals)

            self.env['monday.sync.entry'].log_sync_entry(
                instance=instance,
                operation='Create',
                import_action='Account',
                action_done='Registration',
                status='success'
            )

            return {
                'name': _('Monday Account'),
                'type': 'ir.actions.act_window',
                'res_model': 'monday.instance',
                'res_id': instance.id,
                'view_mode': 'form',
                'target': 'current',
            }

        except Exception as e:
            self.env['monday.sync.entry'].log_sync_entry(
                instance=False,
                operation='Create',
                import_action='Account',
                action_done='Registration',
                status='failed'
            )
            raise UserError(_("Establish Connection Failed: %s") % str(e))
