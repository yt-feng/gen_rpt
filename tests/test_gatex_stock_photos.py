from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from PIL import Image
import unittest
from gen_rpt import stock_photo_assets as stock
from gen_rpt.gatex_whitepaper_pipeline import _generate_visuals, _build_payload


def photo(identifier, keywords, **kwargs):
    return {'id':identifier,'title':identifier,'keywords':keywords,'src':f'/images/stock/{identifier}.jpg',
        'alt':{'en':identifier,'zh':'照片'},'sourcePage':'https://commons.wikimedia.org/wiki/File:Example.jpg',
        'imageUrl':'https://upload.wikimedia.org/unused.jpg','author':'Original photographer','license':'CC BY 3.0',
        'licenseUrl':'https://creativecommons.org/licenses/by/3.0/','width':1600,'height':1000,**kwargs}


CATALOG = [photo('solar',['solar','photovoltaic','renewable energy','光伏']),
    photo('finance',['investment','capital markets','finance']),
    photo('servers',['data center','AI infrastructure','computing']),
    photo('hong-kong',['Hong Kong','香港'],locations=['Hong Kong','香港']),
    photo('frankfurt',['Frankfurt','法兰克福'],locations=['Frankfurt','法兰克福']),
    photo('port',['shipping','logistics','supply chain','Singapore'],locations=['Singapore','新加坡'])]


def image_blob():
    image=Image.effect_noise((1600,1000),75).convert('RGB')
    out=io.BytesIO();image.save(out,format='JPEG',quality=92);return out.getvalue()


class GateXStockPhotoTests(unittest.TestCase):
    def test_summary_cannot_introduce_candidate_or_wrong_energy(self):
        assert stock.select_stock_photo('Saudi Arabia crude oil outlook',CATALOG,summary='Solar capacity is a minor comparison') is None
        assert stock.select_stock_photo('Unrelated restaurant staffing',CATALOG,summary='capital markets investment') is None
        assert stock.select_stock_photo('Saudi Arabia renewable energy investment',CATALOG)['id']=='solar'

    def test_specific_primary_phrase_outweighs_generic_investment(self):
        assert stock.select_stock_photo('Data center investment',CATALOG)['id']=='servers'
        assert stock.select_stock_photo('光伏投资',CATALOG)['id']=='solar'
        assert stock.select_stock_photo('Capital markets investment',CATALOG)['id']=='finance'

    def test_geography_does_not_fake_named_facility(self):
        assert stock.select_stock_photo('Saudi Arabia shipping',CATALOG) is None
        assert stock.select_stock_photo('China supply chain outlook',CATALOG) is None
        assert stock.select_stock_photo('Singapore shipping',CATALOG)['id']=='port'
        assert stock.select_stock_photo('Shipping industry outlook',CATALOG)['id']=='port'
        assert stock.select_stock_photo('Hong Kong industry',CATALOG)['id']=='hong-kong'
        assert stock.select_stock_photo('Frankfurt industry',CATALOG)['id']=='frankfurt'
        assert stock.select_stock_photo('Hong Kong semiconductor research',CATALOG) is None
        assert stock.select_stock_photo('Finance investment',CATALOG)['id']=='finance'

    def test_exact_word_boundary_and_exclusions(self):
        assert stock.select_stock_photo('Said rail infrastructure',CATALOG) is None
        a=stock.select_stock_photo('Solar investment',CATALOG,seed='source1')
        assert stock.select_stock_photo('Solar investment',CATALOG,seed='source1')==a
        assert stock.select_stock_photo('Solar investment',CATALOG,exclude_ids={a['id']}) is None

    def test_stock_crop_retains_canonical_source_and_exact_output_fingerprint(self):
        with TemporaryDirectory() as directory:
            target=Path(directory)/'cover.jpg';blob=image_blob()
            value=stock.save_stock_photo(CATALOG[0],target,size=(1200,1800),portrait_cover=True,blob=blob)
            assert value['source']==stock.stock_photo_source(CATALOG[0])
            assert value['source']['width']==1600 and value['width']==1200
            assert value['height']==1800 and value['cropApplied'] is True
            assert value['sha256']==hashlib.sha256(target.read_bytes()).hexdigest()
            assert value['sourceImageSha256']==hashlib.sha256(blob).hexdigest()
            with Image.open(target) as output:
                assert max(abs(a-b) for a,b in zip(output.getpixel((10,10)),(8,29,56)))<=3
            assert value['source']['author']=='Original photographer'
            assert value['source']['licenseUrl']=='https://creativecommons.org/licenses/by/3.0/'

    def test_photo_download_uses_only_gatex_local_asset(self):
        with patch.object(stock,'_read',return_value=b'photo') as read:
            assert stock.photo_bytes(CATALOG[0])==b'photo'
        read.assert_called_once_with('https://gatex.fund/images/stock/solar.jpg',16*1024*1024)
        with self.assertRaises(ValueError): stock.photo_bytes({**CATALOG[0],'src':'https://supplier.example/image.jpg'})
        with self.assertRaises(ValueError): stock.photo_bytes({**CATALOG[0],'src':'/images/stock/../../private.jpg'})

    def test_catalog_requires_version_local_assets_and_provenance(self):
        with patch.object(stock,'_read',return_value=json.dumps({'version':1,'photos':CATALOG}).encode()):
            assert stock.load_catalog()==CATALOG
        for payload in ({'version':2,'photos':CATALOG},{'version':1,'photos':[{**CATALOG[0],'license':''}]},
            {'version':1,'photos':[{**CATALOG[0],'src':'/images/stock/a/../../secret.jpg'}]}):
            with patch.object(stock,'_read',return_value=json.dumps(payload).encode()),self.assertRaises(ValueError): stock.load_catalog()

    def test_whitepaper_stock_deduplicates_without_ai_or_paid_image_review(self):
        content={'title':'Data center investment','visuals':[{'id':'executive-summary','alt':'servers'}, {'id':'chapter-1','alt':'computing'}]}
        with TemporaryDirectory() as directory,patch.dict('os.environ',{'GATEX_IMAGE_PROVIDER':'stock'}),\
            patch('gen_rpt.gatex_whitepaper_pipeline.load_catalog',return_value=CATALOG),\
            patch.object(stock,'photo_bytes',return_value=image_blob()),\
            patch('gen_rpt.gatex_whitepaper_pipeline._download_apimart_image') as ai,\
            patch('gen_rpt.gatex_whitepaper_pipeline._pollinations_image') as pollinations,\
            patch('gen_rpt.gatex_whitepaper_pipeline.semantic_visual_quality_issues') as paid_qa:
            result=_generate_visuals(content,Path(directory),title=content['title'])
            assert result['executive-summary']['source']['id']=='servers'
            assert result['chapter-1']['path']==''
            assert result['executive-summary']['source']['license']=='CC BY 3.0'
            manifest=json.loads((Path(directory)/'image-sources.json').read_text())
            assert manifest['executive-summary']['source']==stock.stock_photo_source(CATALOG[2])
            ai.assert_not_called();pollinations.assert_not_called();paid_qa.assert_not_called()

    def test_whitepaper_native_text_cover_when_topic_or_catalog_unavailable(self):
        content={'title':'Crude oil outlook','visuals':[{'id':'executive-summary','alt':'solar comparison'}], 'original':'all facts retained'}
        with TemporaryDirectory() as directory,patch.dict('os.environ',{'GATEX_IMAGE_PROVIDER':'stock'}),\
            patch('gen_rpt.gatex_whitepaper_pipeline.load_catalog',return_value=CATALOG),\
            patch('gen_rpt.gatex_whitepaper_pipeline._download_apimart_image') as ai,\
            patch('gen_rpt.gatex_whitepaper_pipeline._pollinations_image') as pollinations:
            result=_generate_visuals(content,Path(directory),title=content['title'])
            assert result['executive-summary']=={'path':'','alt':'','source':None}
            assert content['original']=='all facts retained'
            ai.assert_not_called();pollinations.assert_not_called()
        with TemporaryDirectory() as directory,patch('gen_rpt.gatex_whitepaper_pipeline.load_catalog',side_effect=OSError('unavailable')):
            result=_generate_visuals(content,Path(directory),title=content['title'])
            assert result['executive-summary']['path']==''

    def test_whitepaper_cache_reused_without_network_or_paid_qa(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);(root/'executive-summary.jpg').write_bytes(image_blob())
            source=stock.stock_photo_source(CATALOG[2]);(root/'executive-summary.source.json').write_text(json.dumps(source))
            with patch('gen_rpt.gatex_whitepaper_pipeline.load_catalog') as catalog,patch('gen_rpt.gatex_whitepaper_pipeline.semantic_visual_quality_issues') as paid_qa:
                result=_generate_visuals({'visuals':[{'id':'executive-summary','alt':'Data center'}]},root)
            assert result['executive-summary']['source']==source
            catalog.assert_not_called();paid_qa.assert_not_called()

    def test_cached_same_photo_id_is_not_repeated_after_different_crop(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);source=stock.stock_photo_source(CATALOG[2])
            for key in ("executive-summary","chapter-1"):
                (root/f"{key}.jpg").write_bytes(image_blob())
                (root/f"{key}.source.json").write_text(json.dumps(source))
            with patch("gen_rpt.gatex_whitepaper_pipeline.load_catalog",return_value=[CATALOG[2]]),patch.object(stock,"photo_bytes") as download:
                result=_generate_visuals({"title":"Data center investment","visuals":[{"id":"executive-summary"},{"id":"chapter-1"}]},root)
            assert result["executive-summary"]["source"]["id"]=="servers"
            assert result["chapter-1"]["path"]==""
            download.assert_not_called()

    def test_gatex_original_licensed_photo_precedes_stock_and_ai(self):
        from gen_rpt.image_generator import generate_ai_image_assets
        candidate={"source_title":"Semiconductor wafer plant","caption":"Semiconductor wafer plant", "alt_text":"Semiconductor wafer", "original_image_url":"https://example.org/photo.jpg",
            "source_page_url":"https://example.org/article", "attribution":"Original photographer", "license":"CC BY", "license_url":"https://creativecommons.org/licenses/by/4.0/"}
        def original(_candidate,target,**kwargs):
            Image.effect_noise((1200,800),80).convert("RGB").save(target,format="PNG")
            return "original-fingerprint", ""
        with TemporaryDirectory() as directory:
            root=Path(directory);report={"sections":[{"title":"Plants","paragraphs":["All findings retained."]}]}
            with patch("gen_rpt.image_generator._discover_source_image_candidates",return_value=[candidate]),                patch("gen_rpt.image_generator._download_source_candidate",side_effect=original),                patch("gen_rpt.image_generator.prepare_stock_photo") as selected,                patch("gen_rpt.image_generator._download_pollinations_or_fallback") as ai:
                result=generate_ai_image_assets(object(),"Semiconductor wafers",report,root/"assets",root/"backup",single_editorial_image=True)
            assert result=={"image-1":"assets/image-1.png"}
            assert report["image_assets"][0]["source_image_sha256"]=="original-fingerprint"
            assert report["image_assets"][0]["image_sha256"]==hashlib.sha256((root/"assets"/"image-1.png").read_bytes()).hexdigest()
            assert candidate["license_url"] in report["sections"][0]["image_source"]
            selected.assert_not_called();ai.assert_not_called()

    def test_long_photo_licence_caption_is_retained_by_normalized_renderer(self):
        from gen_rpt.gatex_pdf_renderer import _normalized_section
        caption="Photograph: Original photographer, CC BY-SA. " + "x"*550 + " https://creativecommons.org/licenses/by-sa/2.0/"
        result=_normalized_section({"id":"photo","kind":"section","visualAlt":caption},1)
        assert result["visualAlt"]==caption

    def test_original_licence_restricts_gatex_source_selection(self):
        from gen_rpt.image_generator import _commercial_source_license
        assert _commercial_source_license({'license':'CC BY','license_url':'https://creativecommons.org/licenses/by/4.0/'})
        assert _commercial_source_license({'license':'CC BY-SA','license_url':'https://creativecommons.org/licenses/by-sa/3.0/'})
        assert not _commercial_source_license({'license':'CC BY-NC','license_url':'https://creativecommons.org/licenses/by-nc/4.0/'})
        assert not _commercial_source_license({'license':'CC BY','license_url':'https://creativecommons.org/licenses/by-nc/4.0/'})
        assert not _commercial_source_license({'license':'CC BY','license_url':''})

    def test_stock_cover_credit_and_manifest_are_preserved_in_pdf_payload(self):
        source=stock.stock_photo_source(CATALOG[2])
        content={'executiveSummary':{'headline':'Data center investment','deck':'All facts','paragraphs':['Research text'],'sourceIds':[]},
            'chapters':[],'exhibits':[],'outlook':{'title':'Outlook','deck':'More facts','callout':'','paragraphs':['Retained']},'subtitle':'Research','coverSummary':'Summary'}
        payload=_build_payload(slug='example',title='Data center investment',publication_date='2026-10-15',content=content,sources=[],
            visuals={'executive-summary':{'path':'/tmp/cover.jpg','alt':'Server photograph','source':source}})
        assert payload['coverImagePath']=='/tmp/cover.jpg'
        assert payload['contentSections'][0]['visualPath']==''
        assert payload['contentSections'][0]['paragraphs']==['Research text']
        credit=payload['contentSections'][0]['footnotes'][0]
        assert source['author'] in credit and source['licenseUrl'] in credit and source['sourceUrl'] in credit
        assert payload['imageSources']['executive-summary']==source

if __name__ == "__main__":
    unittest.main()
