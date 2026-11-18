"""Lay out PCB footprints in the same arrangement as their schematic symbols.

Pcbnew action plugin for KiCad 7, 8, 9 and 10 (SWIG Python API).
"""
import json
import os
import re
import traceback

import pcbnew


DEBUG = None

# Schematic positions are stored in units of 0.01 mm and scaled by
# POS_SCALE (nm per unit) when placed on the board.
POS_SCALE = 5000

# Vertical spacing between sheets, as a multiple of each sheet's height.
OFFSET_FACTOR = 1.25


# S-expression tokens: "(", ")", a double-quoted string (with backslash
# escapes) or a bare atom.
_TOKEN_RE = re.compile(r'\(|\)|"([^"\\]*(?:\\.[^"\\]*)*)"|[^\s()"]+', re.S)
_ESCAPE_RE = re.compile(r'\\(.)', re.S)
_ESCAPES = {'n': '\n', 'r': '\r', 't': '\t'}


def _unescape(s):
    if '\\' not in s:
        return s
    return _ESCAPE_RE.sub(lambda m: _ESCAPES.get(m.group(1), m.group(1)), s)


def parse_sexpr(text):
    """Parse a KiCad s-expression file into nested lists of strings."""
    stack = [[]]
    for m in _TOKEN_RE.finditer(text):
        token = m.group(0)
        if token == '(':
            node = []
            stack[-1].append(node)
            stack.append(node)
        elif token == ')':
            if len(stack) == 1:
                raise SyntaxError('unexpected )')
            stack.pop()
        elif m.group(1) is not None:
            stack[-1].append(_unescape(m.group(1)))
        else:
            stack[-1].append(token)
    if len(stack) != 1 or len(stack[0]) != 1:
        raise SyntaxError('unbalanced parentheses')
    return stack[0][0]


# Class to represent a single sheet of a schematic. Has a map from
# component IDs to positions, a map from sub-sheet IDs to sub-sheet
# names and schematic file names, plus coordinate ranges for the
# component positions.
class SchSheet:
    def __init__(self, file):
        self.file = file
        self.components = dict()
        self.sub_sheets = dict()

        print('New sheet from:', file, file=DEBUG)

        self.xrange = [None, None]
        self.yrange = [None, None]

        with open(file, encoding='utf-8') as f:
            self.walk(parse_sexpr(f.read()))

    # Extend x- and y-coordinate ranges based on new component or
    # sub-sheet values.
    def extend_range(self, x, y):
        if self.xrange[0] is None or x < self.xrange[0]:
            self.xrange[0] = x
        if self.xrange[1] is None or x > self.xrange[1]:
            self.xrange[1] = x
        if self.yrange[0] is None or y < self.yrange[0]:
            self.yrange[0] = y
        if self.yrange[1] is None or y > self.yrange[1]:
            self.yrange[1] = y

    def height(self):
        if self.yrange[0] is None:
            return 0
        return self.yrange[1] - self.yrange[0]

    @staticmethod
    def pick(lst, *attribute_names):
        names = set(attribute_names)
        return {item[0]: item[1:] for item in lst
                if isinstance(item, list) and item and item[0] in names}

    @staticmethod
    def pick_property(lst, prop_name):
        for item in lst:
            if (isinstance(item, list) and len(item) > 2
                    and item[0] == 'property' and item[1] == prop_name):
                return item[2]

    def walk(self, ast):
        def position_convert(x):
            return int(float(x) * 100)

        for i in ast:
            if not isinstance(i, list) or not i:
                continue
            token = i[0]
            if token == 'symbol':  # Placed component
                attrs = self.pick(i[1:], 'uuid', 'at')
                component_ref = self.pick_property(i[1:], 'Reference')
                component_id = attrs['uuid'][0]
                component_pos = tuple(map(position_convert, attrs['at'][:2]))
                self.components[component_id] = (component_ref, component_pos)
                self.extend_range(component_pos[0], component_pos[1])
            elif token == 'sheet':  # Sub-sheet
                attrs = self.pick(i[1:], 'uuid', 'at', 'size')
                sheet_bounds = tuple(map(position_convert, attrs['at'][:2] + attrs['size'][:2]))
                sheet_id = attrs['uuid'][0]
                sheet_name = self.pick_property(i[1:], 'Sheetname')
                sheet_file = self.pick_property(i[1:], 'Sheetfile')
                self.sub_sheets[sheet_id] = (sheet_name, sheet_file)
                self.extend_range(sheet_bounds[0], sheet_bounds[1])
                self.extend_range(sheet_bounds[0] + sheet_bounds[2],
                                  sheet_bounds[1] + sheet_bounds[3])


def root_schematic_files(work_dir, project_name):
    """Return the project's top-level schematic files.

    KiCad 10 projects can have several top-level sheets, listed in the
    project file. Older projects have a single root sheet named after
    the project.
    """
    project_file = os.path.join(work_dir, project_name + '.kicad_pro')
    try:
        with open(project_file, encoding='utf-8') as f:
            settings = json.load(f)
        top_level = settings.get('schematic', {}).get('top_level_sheets') or []
        files = [os.path.join(work_dir, s['filename'])
                 for s in top_level if s.get('filename')]
        if files:
            return files
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return [os.path.join(work_dir, project_name + '.kicad_sch')]


def load_sheets(root_files):
    """Read each top-level sheet and its hierarchy, in layout order.

    Returns a list of (sheet_path, SchSheet). Symbols on a top-level sheet
    have footprint paths of the form /<symbol uuid>; symbols on sub-sheets
    are prefixed by the uuids of the sheets above them.
    """
    sheets = []
    for root_file in root_files:
        queue = [('', root_file)]
        while queue:
            sheet_path, file_name = queue.pop(0)
            sheet = SchSheet(file_name)
            print('store to sheet[{}] = {}'.format(sheet_path, file_name), file=DEBUG)
            sheets.append((sheet_path, sheet))
            # Sub-sheet file names are relative to the parent sheet's directory.
            base_dir = os.path.dirname(file_name)
            for sub_id, (_, sub_file) in sheet.sub_sheets.items():
                queue.append((sheet_path + '/' + sub_id,
                              os.path.join(base_dir, sub_file)))
    return sheets


def move_footprints(components, board):
    selection_active = any(fp.IsSelected() for fp in board.GetFootprints())
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        path = '/' + '/'.join([x.AsString() for x in fp.GetPath()])
        print(ref, path, file=DEBUG)
        if path not in components:
            print('  NOT FOUND', file=DEBUG)
            continue
        ref, pos, sheet, offset = components[path]
        if fp.IsLocked():
            print('  path =', path, '  sheet =', sheet, '  ref =', ref, ' is locked, skip', file=DEBUG)
            continue
        new_pos = pcbnew.VECTOR2I(pos[0] * POS_SCALE, (pos[1] + offset) * POS_SCALE)
        print('  path =', path, '  sheet =', sheet, '  ref =', ref,
              '  pos =', pos, '  new_pos =', new_pos, file=DEBUG)
        if not selection_active or fp.IsSelected():
            fp.SetPosition(new_pos)


def show_error(message):
    try:
        import wx
        wx.MessageBox(message, 'Schematic positions -> PCB positions',
                      wx.OK | wx.ICON_ERROR)
    except Exception:
        print(message)


class SchematicPositionsToLayoutPlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "Schematic positions -> PCB positions"
        self.category = "Modify PCB"
        self.description = "Layout components on PCB in same spatial relationships as components on schematic"
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(os.path.dirname(__file__), 'sch2layout.png')

    def Run(self):
        global DEBUG
        board_file = pcbnew.GetBoard().GetFileName()
        if not board_file:
            show_error('Save the board as part of a KiCad project first.')
            return
        work_dir = os.path.dirname(os.path.abspath(board_file))
        debug_file = os.path.join(work_dir, 'schematic-positions-to-layout.debug')
        DEBUG = open(debug_file, 'w', encoding='utf-8')
        try:
            self.DoRun()
        except Exception as e:
            traceback.print_exc(file=DEBUG)
            show_error('{}\n\nDetails are in {}'.format(e, debug_file))
        finally:
            DEBUG.close()
            DEBUG = None

    def DoRun(self):
        board = pcbnew.GetBoard()
        work_dir, in_pcb_file = os.path.split(os.path.abspath(board.GetFileName()))
        project_name = os.path.splitext(in_pcb_file)[0]
        root_files = root_schematic_files(work_dir, project_name)
        print('work_dir = {}'.format(work_dir), file=DEBUG)
        print('in_pcb_file = {}'.format(in_pcb_file), file=DEBUG)
        print('root_schematic_files = {}'.format(root_files), file=DEBUG)

        missing = [f for f in root_files if not os.path.isfile(f)]
        if missing:
            raise FileNotFoundError('Schematic not found: ' + ', '.join(missing))

        # Read schematic sheets, starting at the top-level sheets and
        # following links to sub-sheets.
        sheets = load_sheets(root_files)

        # Lay the sheets out one after another down the page, and make a
        # master component map recording each component's position in its
        # sheet plus that sheet's vertical offset.
        components = dict()
        running_offset = 0
        for sheet_path, s in sheets:
            offset = int(running_offset)
            running_offset += OFFSET_FACTOR * s.height()
            print('Processing', sheet_path or '/', 'offset', offset, file=DEBUG)
            for cid, (ref, pos) in s.components.items():
                components[sheet_path + '/' + cid] = (ref, pos, sheet_path, offset)

        for cid in components:
            print(cid, components[cid], file=DEBUG)

        # Move the components.
        move_footprints(components, board)
        pcbnew.Refresh()


SchematicPositionsToLayoutPlugin().register()
