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


class MondayImportWizard(models.TransientModel):
    """Wizard to select settings and run Import Data on a Monday Instance."""
    _name = 'monday.import.wizard'
    _description = 'Monday Import Wizard'

    instance_id = fields.Many2one('monday.instance', string='Monday Account', required=True)

    import_boards = fields.Boolean(string='Boards')
    import_groups = fields.Boolean(string='Groups')
    import_items = fields.Boolean(string='Items')
    import_users = fields.Boolean(string='Users')

    board_id = fields.Many2one(
        'monday.board',
        string='Monday Board',
        domain="[('instance_id', '=', instance_id)]",
        help="Optional. Specify a board to only import groups or items for that board."
    )

    def action_execute_import(self):
        self.ensure_one()
        if not (self.import_boards or self.import_groups or self.import_items or self.import_users):
            raise UserError(_("Please select at least one entity to import."))

        instance = self.instance_id.with_context(monday_sync_direction='import')
        
        # Initialize counts
        boards_imported = 0
        boards_updated = 0
        groups_imported = 0
        groups_updated = 0
        items_imported = 0
        items_updated = 0
        users_imported = 0
        users_updated = 0

        if self.import_boards:
            res = instance._import_boards()
            boards_imported = res.get('created', 0)
            boards_updated = res.get('updated', 0)

        if self.import_groups:
            if self.board_id:
                try:
                    groups = instance.get_groups(self.board_id.monday_board_id)
                    existing_groups = {g.monday_group_id: g for g in self.board_id.group_ids}
                    imported = 0
                    updated = 0
                    for grp in groups:
                        g_id = grp["id"]
                        g_name = grp["title"]
                        if g_id in existing_groups:
                            existing_groups[g_id].write({'name': g_name})
                            updated += 1
                        else:
                            self.env['monday.group'].create({
                                'board_id': self.board_id.id,
                                'name': g_name,
                                'monday_group_id': g_id
                            })
                            imported += 1
                        if self.board_id.odoo_project_id:
                            stage = self.env['project.task.type'].search([
                                ('name', '=', g_name),
                                ('project_ids', 'in', [self.board_id.odoo_project_id.id])
                            ], limit=1)
                            if not stage:
                                stage = self.env['project.task.type'].search([
                                    ('monday_group_id', '=', g_id)
                                ], limit=1)
                                if not stage:
                                    self.env['project.task.type'].create({
                                        'name': g_name,
                                        'monday_instance_id': instance.id,
                                        'monday_group_id': g_id,
                                        'project_ids': [(4, self.board_id.odoo_project_id.id)]
                                    })
                                else:
                                    stage.write({
                                        'name': g_name,
                                        'monday_instance_id': instance.id,
                                        'project_ids': [(4, self.board_id.odoo_project_id.id)]
                                    })
                            else:
                                stage.write({
                                    'monday_instance_id': instance.id,
                                    'monday_group_id': g_id
                                })
                    self.env['monday.sync.entry'].log_sync_entry(
                        instance=instance,
                        operation='Create',
                        import_action='Groups',
                        action_done='Import',
                        status='success'
                    )
                    groups_imported = imported
                    groups_updated = updated
                except Exception as e:
                    self.env['monday.sync.entry'].log_sync_entry(
                        instance=instance,
                        operation='Create',
                        import_action='Groups',
                        action_done='Import',
                        status='failed'
                    )
                    raise UserError(_("Failed to import groups: %s") % str(e))
            else:
                res = instance._import_groups()
                groups_imported = res.get('created', 0)
                groups_updated = res.get('updated', 0)

        if self.import_items:
            if self.board_id:
                res = instance._import_items_for_board(self.board_id)
                self.env['monday.sync.entry'].log_sync_entry(
                    instance=instance,
                    operation='Create',
                    import_action='Items',
                    action_done='Import',
                    status='success'
                )
                items_imported = res.get('created', 0)
                items_updated = res.get('updated', 0)
            else:
                res = instance._import_items()
                items_imported = res.get('created', 0)
                items_updated = res.get('updated', 0)

        if self.import_users:
            res = instance._import_users()
            users_imported = res.get('created', 0)
            users_updated = res.get('updated', 0)

        total_imported = boards_imported + groups_imported + items_imported + users_imported
        total_updated = boards_updated + groups_updated + items_updated + users_updated

        # Build final summary footer
        boards_html = ""
        boards_style = ""
        if self.import_boards:
            boards_html = """
                <div>• Imported : <strong>%s</strong></div>
                <div>• Updated : <strong>%s</strong></div>
            """ % (boards_imported, boards_updated)
        else:
            boards_html = '<div style="color: #a0aec0; font-style: italic;">• Skipped</div>'
            boards_style = "opacity: 0.6;"

        groups_html = ""
        groups_style = ""
        if self.import_groups:
            board_name = self.board_id.name if self.board_id else _("All Active")
            groups_html = """
                <div class="board-info" title="%s">Board : %s</div>
                <div>• Imported : <strong>%s</strong></div>
                <div>• Updated : <strong>%s</strong></div>
            """ % (board_name, board_name, groups_imported, groups_updated)
        else:
            groups_html = '<div style="color: #a0aec0; font-style: italic;">• Skipped</div>'
            groups_style = "opacity: 0.6;"

        items_html = ""
        items_style = ""
        if self.import_items:
            board_name = self.board_id.name if self.board_id else _("All Active")
            items_html = """
                <div class="board-info" title="%s">Board : %s</div>
                <div>• Imported : <strong>%s</strong></div>
                <div>• Updated : <strong>%s</strong></div>
            """ % (board_name, board_name, items_imported, items_updated)
        else:
            items_html = '<div style="color: #a0aec0; font-style: italic;">• Skipped</div>'
            items_style = "opacity: 0.6;"

        users_html = ""
        users_style = ""
        if self.import_users:
            users_html = """
                <div>• Imported : <strong>%s</strong></div>
                <div>• Updated : <strong>%s</strong></div>
            """ % (users_imported, users_updated)
        else:
            users_html = '<div style="color: #a0aec0; font-style: italic;">• Skipped</div>'
            users_style = "opacity: 0.6;"

        summary_html = """
        <div class="monday-summary-container">
            <h3 class="summary-header">✓ Monday Data Imported Successfully</h3>
            <div class="summary-grid">
                <!-- Boards Card -->
                <div class="summary-card board-card" style="%s">
                    <div class="card-header">%s</div>
                    <div class="card-body">
                        %s
                    </div>
                </div>
                <!-- Groups Card -->
                <div class="summary-card group-card" style="%s">
                    <div class="card-header">%s</div>
                    <div class="card-body">
                        %s
                    </div>
                </div>
                <!-- Items Card -->
                <div class="summary-card item-card" style="%s">
                    <div class="card-header">%s</div>
                    <div class="card-body">
                        %s
                    </div>
                </div>
                <!-- Users Card -->
                <div class="summary-card user-card" style="%s">
                    <div class="card-header">%s</div>
                    <div class="card-body">
                        %s
                    </div>
                </div>
            </div>
            <div class="summary-divider"></div>
            <div class="summary-footer">
                <div class="footer-stats">
                    <span>Total Imported : <strong>%s</strong></span>
                    <span>Total Updated : <strong>%s</strong></span>
                </div>
                <div class="footer-status">%s</div>
            </div>
        </div>
        """ % (
            boards_style, _("Boards"), boards_html,
            groups_style, _("Groups"), groups_html,
            items_style, _("Items"), items_html,
            users_style, _("Users"), users_html,
            total_imported, total_updated, _("No Errors Found.")
        )

        summary_wizard = self.env['monday.import.summary.wizard'].create({
            'summary_html': summary_html
        })

        return {
            'name': _('Import Status'),
            'type': 'ir.actions.act_window',
            'res_model': 'monday.import.summary.wizard',
            'res_id': summary_wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }


class MondayImportSummaryWizard(models.TransientModel):
    """Wizard to display the final import results in form view."""
    _name = 'monday.import.summary.wizard'
    _description = 'Monday Import Summary Wizard'

    summary_html = fields.Html(string='Summary HTML', readonly=True)


class MondayInstanceImportWizard(models.TransientModel):
    """Dummy class to prevent model validation errors in CSV files."""
    _name = 'monday.instance.import.wizard'
    _description = 'Dummy Import Wizard'
    instance_id = fields.Many2one('monday.instance', string='Monday Account')
