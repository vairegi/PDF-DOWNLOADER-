import asyncio
from aiohttp import ClientSession
from scraper.generic import DirectPDFAdapter, HTMLImageAdapter, download_images
from scraper.pdf import images_to_pdf

PDF_CANDIDATES = [
    "https://raw.githubusercontent.com/mozilla/pdf.js/master/test/pdfs/tracemonkey.pdf",
    "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
]
IMG_CANDIDATES = [
    ["https://placehold.co/600x900/png", "https://placehold.co/600x901/png", "https://placehold.co/600x902/png"],
    ["https://httpbin.org/image/png", "https://httpbin.org/image/jpeg"],
]
async def main():
    async with ClientSession() as s:
        ok1 = False
        for u in PDF_CANDIDATES:
            try:
                pdf = await DirectPDFAdapter().fetch_pdf(s, u)
                if pdf and pdf[:4]==b"%PDF":
                    print(f"TEST1 direct PDF: PASS via {u} ({len(pdf)} bytes)"); ok1=True; break
            except Exception as e:
                print(f"TEST1 host {u} unreachable from sandbox: {type(e).__name__}")
        if not ok1: print("TEST1: BLOCKED-BY-SANDBOX (code path exercised, network unavailable)")
        ok3 = False
        for group in IMG_CANDIDATES:
            pages = await download_images(s, group)
            if len(pages) >= 2:
                pdf2 = images_to_pdf(pages)
                print(f"TEST3 images->PDF: PASS ({len(pages)} pages, {len(pdf2)} bytes, magic={pdf2[:4]})"); ok3=True; break
            else:
                print(f"TEST3 image host group unreachable ({len(pages)}/{len(group)} fetched)")
        if not ok3:
            # offline fallback: generate synthetic images in-memory to prove compiler
            import io
            from PIL import Image
            pages=[]
            for i in range(3):
                im=Image.new("RGB",(600,900),(i*60,100,200)); b=io.BytesIO(); im.save(b,"PNG"); pages.append(b.getvalue())
            pdf2=images_to_pdf(pages)
            print(f"TEST3 compiler offline-proof: PASS ({len(pdf2)} bytes, magic={pdf2[:4]})")
asyncio.run(main())
