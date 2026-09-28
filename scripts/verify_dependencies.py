import ast
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import jmcomic
from PIL import Image
import img2pdf
import fitz

root = Path(__file__).resolve().parents[1]
files = list(root.rglob('*.py'))
for file in files:
    compile(file.read_text(encoding='utf-8-sig'), str(file), 'exec')
print(f'Python syntax: {len(files)} files passed')
json.loads((root / '_conf_schema.json').read_text(encoding='utf-8-sig'))

modules = set()
for file in files:
    if 'tests' in file.parts or 'scripts' in file.parts or file.name.startswith('test_'):
        continue
    for node in ast.walk(ast.parse(file.read_text(encoding='utf-8-sig'))):
        if isinstance(node, ast.Import):
            modules.update(n.name.split('.')[0] for n in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            modules.add(node.module.split('.')[0])
for name in sorted(modules - sys.stdlib_module_names - {'astrbot'}):
    importlib.import_module(name)
print('All third-party imports passed (AstrBot provided by host)')

# Execute the actual option builder without importing the AstrBot host or
# starting Pixiv background jobs / making requests to external content sites.
tree = ast.parse((root / 'jm/plugin.py').read_text(encoding='utf-8-sig'))
helper = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'JmHelper')
method = next(n for n in helper.body if isinstance(n, ast.FunctionDef) and n.name == '_build_option')
scope = {}
exec(compile(ast.Module(body=[method], type_ignores=[]), '<actual option builder>', 'exec'), scope)
with tempfile.TemporaryDirectory() as tmp:
    obj = SimpleNamespace(config={}, data_dir=Path(tmp))
    option = scope['_build_option'](obj)
    assert option.plugins['dependencies_strategy'] == 'auto-install'
    assert option.plugins['after_album'][0]['plugin'] == 'img2pdf'
    print('Real JM option initialization with PDF enabled passed')

    # Reproduce the old failure and verify that the new option reaches the
    # upstream installer instead. Do not uninstall packages in the host.
    original_find = importlib.util.find_spec
    def missing_pdf(name, *args, **kwargs):
        return None if name == 'img2pdf' else original_find(name, *args, **kwargs)
    with patch('importlib.util.find_spec', side_effect=missing_pdf):
        try:
            jmcomic.Img2pdfPlugin.check_plugin_dependency({}, 'failed-fast')
        except Exception as exc:
            assert 'img2pdf' in str(exc)
        else:
            raise AssertionError('Old failed-fast failure was not reproduced')
        with patch.object(jmcomic.Img2pdfPlugin, 'install_missing_dependencies') as install:
            scope['_build_option'](obj)
            install.assert_called_once_with(['img2pdf'])
    print('Missing img2pdf regression: old failure reproduced; auto-install invoked')

    image = Path(tmp) / 'page.jpg'
    Image.new('RGB', (64, 96), 'white').save(image)
    pdf = img2pdf.convert(str(image))
    with fitz.open(stream=pdf, filetype='pdf') as document:
        assert len(document) == 1
    print('Real image-to-PDF conversion passed')

