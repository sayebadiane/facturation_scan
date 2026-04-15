# -*- coding: utf-8 -*-
{
    'name': 'Facturation Scan',
    'version': '19.0.0.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Facturation par scan de produits (code-barres)',
    'description': """
        Module de facturation avec scan de code-barres.

        Fonctionnalites :
        - Scan de produits via lecteur USB ou saisie manuelle
        - Gestion du panier en temps reel (quantite, prix, total)
        - Creation automatique de facture client (account.move)
        - Impression PDF de la facture via rapport standard Odoo
        - Interface responsive (desktop & mobile)
    """,
    'author': 'Groupe Axis',
    'depends': ['base', 'account', 'product', 'sale', 'sale_management', 'barcodes'],
    'data': [
        'security/ir.model.access.csv',
        'data/sequence_data.xml',
        'views/product_template_views.xml',
        'views/scan_session_views.xml',
        'views/menu_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'facturation_scan/static/src/xml/barcode_widget.xml',
            'facturation_scan/static/src/js/barcode_widget.js',
            'facturation_scan/static/src/css/scan_style.css',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
