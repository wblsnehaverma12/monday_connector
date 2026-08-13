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

{
    'name': 'Monday.com Odoo Connector',
    'version': '19.0.1.0.0',
    'summary': """""",
    'description': """""",
    'category': 'Project',
    'author': 'Weblytic Labs',
    'company': 'Weblytic Labs',
    'website': 'https://store.weblyticlabs.com',
    'depends': ['base','mail','project','sale',],
    'data': [
        'security/monday_security.xml',
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'wizard/monday_import_wizard_views.xml',
        'wizard/monday_registration_wizard_views.xml',
        'views/monday_views.xml',
        'views/project_stage_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'wbl_monday_connector/static/src/css/monday_kanban.css',
        ],
    },
    'images': ['static/description/banner.png'],
    'license': 'OPL-1',
    'installable': True,
    'application': True,
    'auto_install': False,
}
