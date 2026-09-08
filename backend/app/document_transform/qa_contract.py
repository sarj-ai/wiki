from typing import Literal


type QaCohort = Literal["canary", "corpus"]

PRODUCTION_WIKI_API_ORIGIN = "https://api-wiki.sarj.ai"
PRODUCTION_QA_EXCHANGE_URL = f"{PRODUCTION_WIKI_API_ORIGIN}/internal/document-transform-qa/exchange"
PRODUCTION_QA_BOOTSTRAP_URL = (
    f"{PRODUCTION_WIKI_API_ORIGIN}/internal/document-transform-qa/bootstrap"
)
PRODUCTION_QA_CANARY_PLAN_URL = (
    f"{PRODUCTION_WIKI_API_ORIGIN}/internal/document-transform-qa/canary/plan"
)
PRODUCTION_QA_CANARY_SUBMIT_URL = (
    f"{PRODUCTION_WIKI_API_ORIGIN}/internal/document-transform-qa/canary/submit"
)
PRODUCTION_QA_CANARY_TASK_URL = (
    f"{PRODUCTION_WIKI_API_ORIGIN}/internal/document-transform-qa/canary/task"
)
PRODUCTION_QA_CANARY_RESULT_URL = (
    f"{PRODUCTION_WIKI_API_ORIGIN}/internal/document-transform-qa/canary/result"
)
PRODUCTION_QA_CANARY_EVIDENCE_URL = (
    f"{PRODUCTION_WIKI_API_ORIGIN}/internal/document-transform-qa/canary/evidence"
)

QA_CANARY_DOCUMENT_COUNT = 1
QA_CORPUS_DOCUMENT_COUNT = 20
QA_MAX_TRAVERSED_FOLDERS = 100
QA_MAX_FOLDER_LIST_PAGES = 100
QA_MAX_PERMISSION_LIST_PAGES = 100
QA_MAX_TRAVERSAL_METADATA_BYTES = 4 * 1024 * 1024
QA_MAX_STORED_RESULT_BYTES = 256 * 1024
QA_MAX_PERMISSION_EVIDENCE_BYTES = 16 * 1024
QA_MAX_STORED_FINDINGS = 1_000
QA_MAX_STORED_MODEL_IDS = 100
QA_MAX_STORED_TOOL_VERSIONS = 100


def required_document_count(cohort: QaCohort) -> int:
    return QA_CANARY_DOCUMENT_COUNT if cohort == "canary" else QA_CORPUS_DOCUMENT_COUNT
