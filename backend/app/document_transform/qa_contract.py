from typing import Literal


type QaCohort = Literal["canary", "corpus"]

PRODUCTION_WIKI_API_ORIGIN = "https://api-wiki.sarj.ai"
PRODUCTION_QA_EXCHANGE_URL = f"{PRODUCTION_WIKI_API_ORIGIN}/internal/document-transform-qa/exchange"

QA_CANARY_DOCUMENT_COUNT = 1
QA_CORPUS_DOCUMENT_COUNT = 20
QA_MAX_TRAVERSED_FOLDERS = 100
QA_MAX_FOLDER_LIST_PAGES = 100
QA_MAX_PERMISSION_LIST_PAGES = 100
QA_MAX_TRAVERSAL_METADATA_BYTES = 4 * 1024 * 1024


def required_document_count(cohort: QaCohort) -> int:
    return QA_CANARY_DOCUMENT_COUNT if cohort == "canary" else QA_CORPUS_DOCUMENT_COUNT
