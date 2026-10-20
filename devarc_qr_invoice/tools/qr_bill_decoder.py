from io import BytesIO

from PIL import Image, ImageOps

from .qr_bill_payload import QR_TYPE, QrBillPayloadError

try:
    import pypdfium2 as pdfium
    import zxingcpp
except ImportError:
    pdfium = zxingcpp = None

PDF_SIGNATURE = b"%PDF"
PDF_SIGNATURE_WINDOW = 1024
PDF_RENDER_SCALE = 300 / 72


def decode(data):
    if pdfium is None or zxingcpp is None:
        raise QrBillPayloadError("Reading a QR-bill requires the Python packages zxing-cpp and pypdfium2.")
    if not data:
        raise QrBillPayloadError("The document is empty.")
    if PDF_SIGNATURE in data[:PDF_SIGNATURE_WINDOW]:
        payload_texts = _read_pdf(data)
    else:
        payload_texts = _read_image(_open_image(data))
    if not payload_texts:
        raise QrBillPayloadError("The document contains no Swiss QR-bill.")
    if len(payload_texts) > 1:
        raise QrBillPayloadError(
            f"The document contains {len(payload_texts)} different Swiss QR-bills, import them one by one."
        )
    return payload_texts.pop()


def _read_pdf(data):
    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as error:
        raise QrBillPayloadError(f"The PDF document cannot be read: {error}") from error
    try:
        payload_texts = set()
        for page_index in reversed(range(len(document))):
            payload_texts |= _read_image(document[page_index].render(scale=PDF_RENDER_SCALE).to_pil())
        return payload_texts
    finally:
        document.close()


def _open_image(data):
    try:
        image = Image.open(BytesIO(data))
        image.load()
    except (OSError, Image.DecompressionBombError) as error:
        raise QrBillPayloadError("The file is neither a PDF nor a readable image.") from error
    return ImageOps.exif_transpose(image)


def _read_image(image):
    grayscale = image.convert("L")
    binarizers = (zxingcpp.Binarizer.LocalAverage, zxingcpp.Binarizer.GlobalHistogram, zxingcpp.Binarizer.FixedThreshold)
    for binarizer in binarizers:
        barcodes = zxingcpp.read_barcodes(grayscale, formats=zxingcpp.BarcodeFormat.QRCode, binarizer=binarizer)
        payload_texts = {barcode.text for barcode in barcodes if barcode.text.startswith(QR_TYPE)}
        if payload_texts:
            return payload_texts
    return set()
