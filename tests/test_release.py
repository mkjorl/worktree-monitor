import base64
import importlib.util
from pathlib import Path
import unittest
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'native'))
spec=importlib.util.spec_from_file_location('release_tool',Path(__file__).resolve().parents[1]/'native/release.py')
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.config={'repository':'mkjorl/worktree-monitor','feed_url':'https://github.com/mkjorl/worktree-monitor/releases/latest/download/appcast.xml',
                     'public_key':base64.b64encode(bytes(32)).decode(),'signing_account':'worktree-monitor-mkjorl'}
        self.info={'CFBundleShortVersionString':'0.1.26','CFBundleVersion':'27','LSMinimumSystemVersion':'13.0'}
        self.signature=base64.b64encode(bytes(64)).decode()

    def test_feed_matches_exact_release_archive_and_uses_build_number_for_ordering(self):
        xml=release.make_appcast(self.config,self.info,'Worktree-Monitor-0.1.26-arm64.zip',123,self.signature)
        item=ET.fromstring(xml).find('channel/item');ns={'sparkle':release.SPARKLE_NS}
        self.assertEqual(item.find('sparkle:version',ns).text,'27')
        self.assertEqual(item.find('sparkle:shortVersionString',ns).text,'0.1.26')
        self.assertEqual(item.find('sparkle:minimumSystemVersion',ns).text,'13.0.0')
        self.assertEqual(item.find('sparkle:hardwareRequirements',ns).text,'arm64')
        enclosure=item.find('enclosure')
        self.assertEqual(enclosure.get('url'),'https://github.com/mkjorl/worktree-monitor/releases/download/v0.1.26/Worktree-Monitor-0.1.26-arm64.zip')
        self.assertEqual(enclosure.get('{'+release.SPARKLE_NS+'}edSignature'),self.signature)
        self.assertEqual(enclosure.get('length'),'123')

    def test_existing_release_tags_must_resolve_to_the_pushed_commit(self):
        head='a'*40;other='b'*40;tag='v0.1.26'
        self.assertFalse(release.validate_tag_target('',tag,head))
        self.assertTrue(release.validate_tag_target(f'{head} refs/tags/{tag}\n',tag,head))
        self.assertTrue(release.validate_tag_target(f'{other} refs/tags/{tag}\n{head} refs/tags/{tag}^{{}}\n',tag,head))
        with self.assertRaises(ValueError):release.validate_tag_target(f'{other} refs/tags/{tag}\n',tag,head)
        with self.assertRaises(ValueError):release.validate_tag_target(f'{head} refs/tags/{tag}\n{other} refs/tags/{tag}^{{}}\n',tag,head)

    def test_fingerprint_detects_changed_runtime_inputs_but_ignores_build_cache(self):
        from source_fingerprint import APP_INPUTS, source_fingerprint
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'native').mkdir()
            for name in APP_INPUTS:(root/name).write_bytes(b'original')
            (root/'native/build.sh').write_text('build')
            before=source_fingerprint(root)
            (root/'.build').mkdir();(root/'.build/generated').write_text('cache')
            self.assertEqual(source_fingerprint(root),before)
            (root/'dashboard.py').write_text('changed')
            self.assertNotEqual(source_fingerprint(root),before)

    def test_invalid_archive_signature_metadata_or_update_source_is_rejected(self):
        for key,value in [('repository','attacker/repo'),('feed_url','http://github.com/mkjorl/worktree-monitor/releases/latest/download/appcast.xml'),('public_key','short'),('signing_account','owner; command')]:
            with self.subTest(key=key),self.assertRaises((ValueError,KeyError)):
                release.make_appcast({**self.config,key:value},self.info,'Worktree-Monitor-0.1.26-arm64.zip',123,self.signature)
        for name,length,signature in [('other.zip',123,self.signature),('Worktree-Monitor-0.1.26-arm64.zip',0,self.signature),('Worktree-Monitor-0.1.26-arm64.zip',123,'short')]:
            with self.assertRaises(ValueError):release.make_appcast(self.config,self.info,name,length,signature)
        for key,value in [('CFBundleShortVersionString','v1;run'),('CFBundleVersion','abc'),('LSMinimumSystemVersion','13.invalid')]:
            with self.assertRaises(ValueError):release.make_appcast(self.config,{**self.info,key:value},'Worktree-Monitor-0.1.26-arm64.zip',123,self.signature)
