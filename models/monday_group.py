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


class MondayGroup(models.Model):
    """Representing Groups inside a Monday board."""
    _name = 'monday.group'
    _description = 'Monday Group'

    name = fields.Char(string='Group Name', required=True)
    monday_group_id = fields.Char(string='Monday Group ID', required=True)
    board_id = fields.Many2one('monday.board', string='Monday Board', required=True, ondelete='cascade')

    _sql_constraints = [
        ('uniq_group_board', 'unique(monday_group_id, board_id)', 'Group ID must be unique per Board!')
    ]
