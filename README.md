# Miscellaneous plugins for KiCad

## Reproduce schematic layout in PCB layout

Based on an original idea by @jenschr

This plugin helps with the initial organisation of parts when first
creating a PCB layout from a schematic.

It uses KiCad's IPC API (the plugin system that replaces the old SWIG
Python plugins), and is tested with KiCad 10. It should also work with
KiCad 9.0.3 or later, but that has not been tested. For KiCad 7 and 8,
use the older SWIG version of this plugin from commit `803be95`.

### Requirements

- In KiCad, open "Preferences" -> "Plugins" and tick "Enable KiCad
  API". The "Python Interpreter" setting there must point to a Python 3
  installation ("Detect Automatically" usually finds one). On Linux,
  that Python needs the `venv` module (on Debian and Ubuntu, install
  the `python3-venv` package).
- Internet access the first time KiCad loads the plugin. KiCad creates
  a Python environment for it and installs the `kicad-python` package
  listed in `requirements.txt`.

### Installation

Copy this folder (`plugin.json`, `requirements.txt`,
`SchematicPositionsToLayout.py` and `sch2layout.png`) into its own
folder inside KiCad's plugins directory, then restart KiCad:

- Linux: `~/.local/share/kicad/10.0/plugins/`
- Windows: `Documents\KiCad\10.0\plugins\`
- macOS: `~/Documents/KiCad/10.0/plugins/`

This is not the `scripting/plugins` folder used by the old SWIG
plugins.

### Usage

1. Save the schematic. The plugin reads the schematic files from disk.

2. Create a new PCB layout and run "Tools" -> "Update PCB from
   Schematic..." to bring in the footprints.

3. Click the "Schematic positions -> PCB positions" button on the PCB
   editor's toolbar.

The result of this action will be that the component footprints in
the PCB editor will be laid out in the same pattern as their
corresponding components in the schematic. Hierarchical sheets, and
each top-level sheet of a KiCad 10 project with several of them, are
laid out one after another down the page in non-overlapping areas.

If any footprints are selected, only those footprints are moved.
Locked footprints are never moved.

The movement of the component footprints by the plugin is a single
editing action, so can be undone in one step if you don't like what
you see.

If something goes wrong, KiCad reports the plugin's error message. A
log of each run is written to `schematic-positions-to-layout.debug` in
the project folder.

Note that the footprint organisation produced by this script is
intended only to aid with keeping track of where everything is at the
very start of component placement. For example, if you have a large
number of decoupling capacitors for a component, you can place them
near to the component in the schematic and the plugin will move them
to be near to the component in the layout too. Another example where
this might be useful is for analog sections where it might be useful
to mirror signal flow and component placement on the schematic in an
initial view of the PCB layout.
