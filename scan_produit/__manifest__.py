# -*- coding: utf-8 -*-
{
    'name': 'Axis - Facturation',
    'version': '18.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Module de facturation personnalisé Groupe Axis',
    'description': """
        Module de facturation étendu pour Groupe Axis.

        Fonctionnalités :
        - Champs supplémentaires sur les factures (référence client, commercial, etc.)
        - Séquences de numérotation personnalisées
        - Vues enrichies avec filtres et regroupements avancés
        - Tableau de bord facturation
    """,
    'author': 'Groupe Axis',
    'depends': ['account', 'sale'],
    'data': [
        'security/ir.model.access.csv',
        'data/sequence_data.xml',
        'views/account_move_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
