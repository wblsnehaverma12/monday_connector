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


class MondayMapping(models.Model):
    """Configures the fields mapping between Odoo models and Monday columns."""
    _name = 'monday.mapping'
    _description = 'Monday Field Mapping'

    name = fields.Char(string='Name', compute='_compute_name', store=True)
    instance_id = fields.Many2one('monday.instance', string='Monday Instance', required=True, ondelete='cascade')
    odoo_model_id = fields.Many2one('ir.model', string='Odoo Model', required=True, ondelete='cascade')
    odoo_model_name = fields.Char(related='odoo_model_id.model', string='Odoo Model Name', readonly=True, store=True)
    odoo_field_id = fields.Many2one('ir.model.fields', string='Odoo Field', required=True, ondelete='cascade')
    odoo_field_name = fields.Char(related='odoo_field_id.name', string='Odoo Field Name', readonly=True, store=True)

    monday_board_id = fields.Many2one('monday.board', string='Monday Board', required=True, ondelete='cascade')
    monday_column_id = fields.Char(string='Monday Column ID', required=True)
    monday_column_type = fields.Selection([
        ('text', 'Text'),
        ('numbers', 'Numbers'),
        ('date', 'Date'),
        ('status', 'Status'),
        ('people', 'People'),
        ('email', 'Email'),
        ('phone', 'Phone'),
        ('dropdown', 'Dropdown'),
        ('checkbox', 'Checkbox'),
        ('timeline', 'Timeline'),
        ('tags', 'Tags'),
        ('long_text', 'Long Text'),
        ('link', 'Link'),
        ('files', 'Files'),
        ('mirror', 'Mirror'),
        ('formula', 'Formula')
    ], string='Monday Column Type', required=True, default='text')

    direction = fields.Selection([
        ('odoo_to_monday', 'Odoo to Monday.com'),
        ('monday_to_odoo', 'Monday.com to Odoo'),
        ('two_way', 'Two-Way Sync')
    ], string='Direction', required=True, default='two_way')

    transform_function = fields.Text(
        string='Transform Function',
        help="Optional Python code to transform values. Use 'record', 'value' and set 'result'. Example: result = value.upper()"
    )
    default_value = fields.Char(string='Default Value')
    required = fields.Boolean(string='Required', default=False)

    @api.depends('odoo_field_id', 'monday_column_id', 'monday_board_id')
    def _compute_name(self):
        for rec in self:
            f_name = rec.odoo_field_id.field_description or rec.odoo_field_name or 'New'
            b_name = rec.monday_board_id.name or 'Board'
            rec.name = f"{f_name} ↔ {rec.monday_column_id} ({b_name})"
