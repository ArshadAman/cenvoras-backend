"""Tenant-partitioned Cloudinary storage for HR employee documents."""

from django.utils.deconstruct import deconstructible
from cloudinary_storage.storage import RESOURCE_TYPES, MediaCloudinaryStorage

IMAGE_EXTENSIONS = {
    'jpg', 'jpeg', 'jpe', 'png', 'gif', 'webp', 'bmp', 'tif', 'tiff',
}


@deconstructible
class TenantHRDocumentStorage(MediaCloudinaryStorage):
    """
    Store HR/KYC files in Cloudinary as:
      cenvoras/tenants/<tenant_id>/employees/<employee_id>/<doc_type>/<uuid>
    Images use the image resource type; PDFs and other files use raw.
    """

    TAG = 'hr-documents'
    RESOURCE_TYPE = RESOURCE_TYPES['RAW']

    def _get_prefix(self):
        return 'cenvoras/'

    def _get_resource_type(self, name):
        ext = str(name).rsplit('.', 1)[-1].lower() if '.' in str(name) else ''
        if ext in IMAGE_EXTENSIONS:
            return RESOURCE_TYPES['IMAGE']
        return RESOURCE_TYPES['RAW']


def hr_document_storage():
    from django.core.files.storage import storages
    return storages['hr_documents']
