import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('reader_settings',Path(__file__).resolve().parents[1]/'reader_settings.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
base='https://reader.example.com/reader'
url=base+'/r/'+'a'*32+'/'

def test_own_domain_allowed():
    assert m.validate_reader_url(url,{'public_base':base})==url

@pytest.mark.parametrize('bad',[url.replace('reader.example.com','other.example.com'),url.replace('https:','http:'),url+'?redirect=evil',url.replace('/r/','/api/'),url.replace('a'*32,'a'),url.replace('reader.example.com','reader.example.com@evil.example')])
def test_wrong_origin_or_path_rejected(bad):
    with pytest.raises(ValueError):m.validate_reader_url(bad,{'public_base':base})

def test_missing_public_base_rejected():
    with pytest.raises(ValueError):m.validate_reader_url(url,{})
