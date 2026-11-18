# -*- coding: utf-8 -*-
"""Lora - Draw Area Boundary command. Python 2.7 / Python 3 source."""
import os
import sys
import importlib
from pyrevit import forms, script, revit

logger = script.get_logger()

try:
    app = revit.doc.Application
    version = int(app.VersionNumber)
    if version < 2023 or version > 2026:
        forms.alert(
            'Draw Area Boundary supports Revit 2023-2026. Detected: {}'.format(app.VersionNumber),
            title='Lora | Unsupported Revit Version', warn_icon=True
        )
        script.exit()

    logger.info('Lora Draw Area Boundary | Revit %s | Python %s',
                app.VersionNumber, sys.version.replace('\\n', ' '))

    lib_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'lib'))
    helper_path = os.path.join(lib_path, 'LoraAreaBoundary.py')
    if not os.path.isfile(helper_path):
        forms.alert('Lora Area Boundary helper is missing:\\n{}'.format(helper_path),
                    title='Lora | Installation Error')
        script.exit()

    if lib_path not in sys.path:
        sys.path.insert(0, lib_path)

    area_tool = importlib.import_module('LoraAreaBoundary')
    try:
        importlib.reload(area_tool)
    except Exception:
        pass
    area_tool.run()

except SystemExit:
    raise
except Exception as ex:
    logger.exception('Draw Area Boundary failed')
    forms.alert(
        'Lora Draw Area Boundary encountered an unexpected error:\\n\\n{}\\n\\n'
        'Revit version: {}\\nPython: {}'.format(
            ex, getattr(revit.doc.Application, 'VersionNumber', 'Unknown'), sys.version.split()[0]
        ),
        title='Lora | Draw Area Boundary Error', warn_icon=True
    )
