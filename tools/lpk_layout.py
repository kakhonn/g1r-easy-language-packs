"""Where `lpk.py split` puts each row: the pack layout.

`split` builds its rows per sheet (01_red_blue ... 13_dex_gen3, see
lpk.SPLIT_FILES); `organize` turns those sheets into {relative path: rows}.
Stdlib only, like lpk.py.

Layouts:
  flat       the 13 sheets in the pack root (the original layout)
  organized  the sheets grouped by generation, with each base game's
             additions in a subfolder (G1/Yellow, G2/Crystal, G3/RSE/Emerald)
"""
import collections

LAYOUTS = ("organized", "flat")
DEFAULT_LAYOUT = "organized"

# sheet -> path in the organized layout
ORGANIZED_PATHS = {
    "01_red_blue": "G1/01_red_blue.csv",
    "02_yellow_exclusive": "G1/Yellow/02_yellow_exclusive.csv",
    "08_names_gen1": "G1/08_names_gen1.csv",
    "11_dex_gen1": "G1/11_dex_gen1.csv",
    "03_gold_silver": "G2/03_gold_silver.csv",
    "04_crystal_exclusive": "G2/Crystal/04_crystal_exclusive.csv",
    "09_names_gen2": "G2/09_names_gen2.csv",
    "12_dex_gen2": "G2/12_dex_gen2.csv",
    "05_firered_leafgreen": "G3/FRLG/05_firered_leafgreen.csv",
    "06_ruby_sapphire": "G3/RSE/06_ruby_sapphire.csv",
    "07_emerald_exclusive": "G3/RSE/Emerald/07_emerald_exclusive.csv",
    "10_names_gen3": "G3/10_names_gen3.csv",
    "13_dex_gen3": "G3/13_dex_gen3.csv",
}


def sheet_path(sheet, layout):
    """Relative path (with / separators) of one of split's sheets."""
    if layout == "flat":
        return sheet + ".csv"
    return ORGANIZED_PATHS[sheet]


def organize(sheets, layout):
    """{sheet: rows} (in lpk.SPLIT_FILES order) -> {relative path: rows}.
    Row order within a file is kept."""
    out = collections.OrderedDict()
    for sheet, rows in sheets.items():
        out.setdefault(sheet_path(sheet, layout), []).extend(rows)
    return out
