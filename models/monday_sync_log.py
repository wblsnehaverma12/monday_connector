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
from datetime import datetime, timedelta


class MondaySyncLog(models.Model):
    """Stores logs of GraphQL API requests/responses for debugging and audit."""
    _name = 'monday.sync.log'
    _description = 'Monday Sync Log'
    _order = 'create_date desc'

    name = fields.Char(string='Log Summary', required=True)
    instance_id = fields.Many2one('monday.instance', string='Monday Instance', ondelete='cascade', required=True)
    direction = fields.Selection([
        ('import', 'Import'),
        ('export', 'Export'),
        ('webhook', 'Webhook')
    ], string='Direction', required=True, default='export')
    operation = fields.Char(string='Operation')
    request_data = fields.Text(string='Request Data')
    response_data = fields.Text(string='Response Data')
    duration = fields.Float(string='Duration (sec)')
    status = fields.Selection([
        ('success', 'Success'),
        ('failed', 'Failed')
    ], string='Status', required=True, default='success')
    error_message = fields.Text(string='Error Message')
    traceback = fields.Text(string='Traceback')
    active = fields.Boolean(string='Active', default=True)

    @api.model
    def log_api_call(self, instance_id, direction, operation, request_data, response_data, duration, status, error_message=None, traceback=None):
        """Helper to create log entries without interrupting active transaction."""
        val = {
            'name': f"{operation.replace('_', ' ').title()} - {status.title()}",
            'instance_id': instance_id,
            'direction': direction,
            'operation': operation,
            'request_data': request_data,
            'response_data': response_data,
            'duration': duration,
            'status': status,
            'error_message': error_message,
            'traceback': traceback
        }
        return self.create(val)

    def action_archive(self):
        self.write({'active': False})

    def cleanup_old_logs(self):
        """Cron job to clean logs older than 15 days."""
        limit_date = datetime.now() - timedelta(days=15)
        old_logs = self.search([
            ('create_date', '<', limit_date),
            ('active', '=', True)
        ])
        old_logs.write({'active': False})
        return len(old_logs)
