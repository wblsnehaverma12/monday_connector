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

from odoo import models, fields


class MondayRecordMapping(models.Model):
    """Maintains relation mapping between Odoo Records and Monday.com Items."""
    _name = 'monday.record.mapping'
    _description = 'Monday Record Mapping'
    _rec_name = 'monday_item_id'

    instance_id = fields.Many2one('monday.instance', string='Instance', required=True, ondelete='cascade')
    odoo_model = fields.Char(string='Odoo Model', required=True, index=True)
    odoo_id = fields.Integer(string='Odoo Record ID', required=True, index=True)
    monday_item_id = fields.Char(string='Monday Item ID', required=False, index=True)
    monday_board_id = fields.Many2one('monday.board', string='Monday Board', required=True, ondelete='cascade')
    monday_group_id = fields.Many2one('monday.group', string='Monday Group', ondelete='set null')
    sync_status = fields.Selection([
        ('synced', 'Synced'),
        ('failed', 'Failed'),
        ('pending', 'Pending')
    ], string='Sync Status', default='synced', index=True)
    last_sync = fields.Datetime(string='Last Sync')

    _uniq_record_mapping = models.Constraint(
        'UNIQUE(instance_id, odoo_model, odoo_id)',
        'Odoo record mapping must be unique per instance!'
    )

