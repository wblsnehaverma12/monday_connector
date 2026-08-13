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

class MondaySyncEntry(models.Model):
    _name = 'monday.sync.entry'
    _description = 'Monday Sync Entry'
    _order = 'request_date desc, id desc'

    name = fields.Char(string='Name', default='Sync Entry', required=True)
    request_date = fields.Datetime(string='Request Date and Time', default=fields.Datetime.now, required=True)
    operation = fields.Char(string='Operation', default='Create')
    import_action = fields.Char(string='Import Action')  # Account, Users, Boards, Groups, Items
    action_done = fields.Char(string='Action Done')      # Registration, Import
    instance_id = fields.Many2one('monday.instance', string='Account', ondelete='cascade')
    status = fields.Selection([
        ('success', 'Success'),
        ('failed', 'Failed')
    ], string='Status', default='success', required=True)

    @api.model
    def log_sync_entry(self, instance, operation, import_action, action_done, status):
        """Helper to create a sync entry log."""
        self.create({
            'name': 'Sync Entry',
            'instance_id': instance.id if instance else False,
            'operation': operation,
            'import_action': import_action,
            'action_done': action_done,
            'status': status,
            'request_date': fields.Datetime.now(),
        })
