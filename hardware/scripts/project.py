"""Write KiCad project files: .kicad_pro (design rules / net classes), lib tables, custom DRC rules."""
import json
import os

# JLCPCB 4-layer, 0.8 mm capability-safe rules (min trace/space 0.09 mm; we use 0.1 / 0.1)
RULES = {
    'allow_blind_buried_vias': False, 'allow_microvias': False,
    'max_error': 0.005,
    'min_clearance': 0.1,
    'min_connection': 0.1,
    'min_copper_edge_clearance': 0.25,
    'min_groove_width': 0.0,
    'min_hole_clearance': 0.2,
    'min_hole_to_hole': 0.25,
    'min_microvia_diameter': 0.2, 'min_microvia_drill': 0.1,
    'min_resolved_spokes': 1,
    'min_silk_clearance': 0.0,
    'min_text_height': 0.5, 'min_text_thickness': 0.08,
    'min_through_hole_diameter': 0.2,
    'min_track_width': 0.1,
    'min_via_annular_width': 0.1,
    'min_via_diameter': 0.4,
    'solder_mask_to_copper_clearance': 0.0,
    'use_height_for_length_calcs': True,
}

NETCLASSES = [
    dict(name='Default', clearance=0.1, track_width=0.15, via_diameter=0.4, via_drill=0.2),
    dict(name='Power', clearance=0.1, track_width=0.3, via_diameter=0.4, via_drill=0.2),
    dict(name='RF', clearance=0.1, track_width=0.2, via_diameter=0.4, via_drill=0.2),
    dict(name='USB', clearance=0.1, track_width=0.18, via_diameter=0.4, via_drill=0.2),
]
PATTERNS = {
    'ana-main': [('Power', 'VSYS'), ('Power', '3V3'), ('Power', 'GND'), ('RF', 'LNA_IN'), ('RF', 'RF_MID'),
                 ('RF', 'RF_FEED'), ('USB', 'USB_DP'), ('USB', 'USB_DN')],
    'ana-power': [('Power', 'VSYS'), ('Power', 'VSYS_RAW'), ('Power', 'VBAT'), ('Power', 'BATN'), ('Power', 'GND'),
                  ('Power', 'VBUS'), ('Power', 'PROT_D'), ('Power', 'VBAT_BT'), ('USB', 'USB_DP'), ('USB', 'USB_DN')],
}

DRU = """(version 1)
# ANA temple boards - extra DRC rules (JLCPCB 4-layer 0.8 mm)
(rule "Board edge (thin temple board)"
  (constraint edge_clearance (min 0.25)))
(rule "Via to pad on fine pitch"
  (condition "A.Type == 'Via' && B.Type == 'Pad'")
  (constraint hole_clearance (min 0.15)))
"""


def write(proj_dir, name):
    os.makedirs(proj_dir, exist_ok=True)
    classes = []
    for nc in NETCLASSES:
        classes.append({
            'bus_width': 12, 'clearance': nc['clearance'], 'diff_pair_gap': 0.2, 'diff_pair_via_gap': 0.25,
            'diff_pair_width': 0.18, 'line_style': 0, 'microvia_diameter': 0.3, 'microvia_drill': 0.1,
            'name': nc['name'], 'pcb_color': 'rgba(0, 0, 0, 0.000)', 'priority': 0 if nc['name'] != 'Default' else 2147483647,
            'schematic_color': 'rgba(0, 0, 0, 0.000)', 'track_width': nc['track_width'],
            'via_diameter': nc['via_diameter'], 'via_drill': nc['via_drill'], 'wire_width': 6})
    pro = {
        'board': {
            'design_settings': {
                'defaults': {'board_outline_line_width': 0.05, 'copper_line_width': 0.2, 'copper_text_size_h': 1.0,
                             'copper_text_size_v': 1.0, 'copper_text_thickness': 0.15, 'other_line_width': 0.1,
                             'silk_line_width': 0.1, 'silk_text_size_h': 0.6, 'silk_text_size_v': 0.6,
                             'silk_text_thickness': 0.1},
                'diff_pair_dimensions': [], 'drc_exclusions': [],
                'rule_severities': {'silk_overlap': 'ignore', 'silk_over_copper': 'warning',
                                    'silk_edge_clearance': 'ignore', 'text_height': 'warning',
                                    'text_thickness': 'warning', 'lib_footprint_issues': 'ignore',
                                    'lib_footprint_mismatch': 'ignore', 'footprint_type_mismatch': 'ignore',
                                    'courtyards_overlap': 'error', 'missing_courtyard': 'ignore',
                                    'isolated_copper': 'warning', 'starved_thermal': 'warning'},
                'rules': RULES,
                'track_widths': [0.0, 0.1, 0.15, 0.18, 0.25, 0.4, 0.5],
                'via_dimensions': [{'diameter': 0.0, 'drill': 0.0}, {'diameter': 0.4, 'drill': 0.2},
                                   {'diameter': 0.5, 'drill': 0.25}],
                'zones_allow_external_fillets': False,
            },
            'layer_presets': [], 'viewports': [],
        },
        'boards': [], 'cvpcb': {'equivalence_files': []},
        'erc': {'erc_exclusions': [], 'meta': {'version': 0}, 'pin_map': [],
                'rule_severities': {'lib_symbol_issues': 'warning', 'lib_symbol_mismatch': 'warning',
                                    'footprint_link_issues': 'warning', 'simulation_model_issue': 'ignore'}},
        'libraries': {'pinned_footprint_libs': [], 'pinned_symbol_libs': []},
        'meta': {'filename': name + '.kicad_pro', 'version': 3},
        'net_settings': {'classes': classes, 'meta': {'version': 4}, 'net_colors': None,
                         'netclass_assignments': None,
                         'netclass_patterns': [{'netclass': c, 'pattern': p} for c, p in PATTERNS.get(name, [])]},
        'pcbnew': {'last_paths': {}, 'page_layout_descr_file': ''},
        'schematic': {'legacy_lib_dir': '', 'legacy_lib_list': []},
        'sheets': [], 'text_variables': {},
    }
    with open(os.path.join(proj_dir, name + '.kicad_pro'), 'w') as f:
        json.dump(pro, f, indent=2)
    with open(os.path.join(proj_dir, 'sym-lib-table'), 'w') as f:
        f.write('(sym_lib_table\n  (version 7)\n  (lib (name "ana")(type "KiCad")(uri "${KIPRJMOD}/../lib/ana.kicad_sym")'
                '(options "")(descr "ANA project symbols"))\n)\n')
    with open(os.path.join(proj_dir, 'fp-lib-table'), 'w') as f:
        f.write('(fp_lib_table\n  (version 7)\n  (lib (name "ana")(type "KiCad")(uri "${KIPRJMOD}/../lib/ana.pretty")'
                '(options "")(descr "ANA project footprints"))\n)\n')
    with open(os.path.join(proj_dir, name + '.kicad_dru'), 'w') as f:
        f.write(DRU)
