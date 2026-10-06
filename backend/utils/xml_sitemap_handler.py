import logging
import os
from typing import List, Optional

try:
    from lxml import etree
except ImportError:
    logging.critical("'lxml' library not found. XML parsing will fail.")
    etree = None

try:
    from llama_index.core.schema import Document
except ImportError:
    logging.critical("llama-index-core not found.")
    Document = None

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
URL_TAG = "{http://www.sitemaps.org/schemas/sitemap/0.9}url"
LOC_TAG = "{http://www.sitemaps.org/schemas/sitemap/0.9}loc"
SITEMAP_INDEX_TAG = "{http://www.sitemaps.org/schemas/sitemap/0.9}sitemap"
SITEMAP_ROOT_TAGS = (
    "{http://www.sitemaps.org/schemas/sitemap/0.9}urlset",
    "{http://www.sitemaps.org/schemas/sitemap/0.9}sitemapindex",
)


def is_sitemap(file_path: str) -> bool:
    """Whether the file's root element is a sitemap <urlset> or <sitemapindex>.

    Reads only up to the root element's start tag.
    """
    if not etree:
        return False
    try:
        with open(file_path, "rb") as f:
            for _, elem in etree.iterparse(f, events=("start",)):
                return elem.tag in SITEMAP_ROOT_TAGS
    except Exception as e:
        logger.debug("Not a sitemap (%s): %s", file_path, e)
    return False


def parse_sitemap(
    file_path: str, doc_id_prefix: Optional[str] = None
) -> List[Document]:
    """Parse an XML sitemap into LlamaIndex Document objects, one per URL.

    A sitemap index yields the URLs of the sitemaps it lists.
    """
    if not etree or not Document:
        logger.error("Missing lxml or llama-index library.")
        return []

    documents: List[Document] = []
    url_count = 0
    is_sitemap_index = False
    filename = os.path.basename(file_path)

    try:
        context = etree.iterparse(
            file_path, events=("end",), tag=[URL_TAG, SITEMAP_INDEX_TAG]
        )
        for _, elem in context:
            if elem.tag == SITEMAP_INDEX_TAG:
                is_sitemap_index = True

            if elem.tag in (URL_TAG, SITEMAP_INDEX_TAG):
                loc_element = elem.find(LOC_TAG, namespaces=SITEMAP_NS)
                if loc_element is not None and loc_element.text:
                    url_text = loc_element.text.strip()
                    if url_text:
                        url_count += 1
                        metadata = {
                            "source_filename": filename,
                            "content_type": (
                                "sitemap_index_url" if elem.tag == SITEMAP_INDEX_TAG
                                else "sitemap_url"
                            ),
                        }
                        doc_id = f"{doc_id_prefix or filename}_url_{url_count}"
                        documents.append(
                            Document(id_=doc_id, text=url_text, metadata=metadata)
                        )
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]
        del context
        if is_sitemap_index and not documents:
            logger.warning("Sitemap index detected but no URL entries processed.")
        if not documents and not is_sitemap_index:
            logger.warning("No URL entries found in sitemap.")
    except FileNotFoundError:
        logger.error("Sitemap file not found: %s", file_path)
    except etree.XMLSyntaxError as e:
        logger.error(
            "XML syntax error parsing sitemap %s: %s", file_path, e, exc_info=True
        )
    except Exception as e:
        logger.error(
            "Unexpected error parsing sitemap %s: %s", file_path, e, exc_info=True
        )

    return documents
