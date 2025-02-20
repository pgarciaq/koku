#
# Copyright 2021 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Report Processor base class."""
import csv
import gzip
import io
import logging

import ciso8601
from dateutil.relativedelta import relativedelta

from api.common import log_json
from api.models import Provider
from masu.database.provider_db_accessor import ProviderDBAccessor
from masu.database.report_manifest_db_accessor import ReportManifestDBAccessor
from masu.exceptions import MasuProcessingError
from masu.external import GZIP_COMPRESSED
from masu.external.date_accessor import DateAccessor
from masu.processor import ALLOWED_COMPRESSIONS
from reporting_common import REPORT_COLUMN_MAP

LOG = logging.getLogger(__name__)


class ReportProcessorBase:
    """
    Download cost reports from a provider.

    Base object class for downloading cost reports from a cloud provider.
    """

    def __init__(self, schema_name, report_path, compression, provider_uuid, manifest_id, processed_report):
        """Initialize the report processor base class.

        Args:
            schema_name (str): The name of the customer schema to process into
            report_path (str): Where the report file lives in the file system
            compression (CONST): How the report file is compressed.
                Accepted values: UNCOMPRESSED, GZIP_COMPRESSED

        """
        if compression.upper() not in ALLOWED_COMPRESSIONS:
            err_msg = f"Compression {compression} is not supported."
            raise MasuProcessingError(err_msg)

        self._schema = schema_name
        self._report_path = report_path
        self._compression = compression.upper()
        self._provider_uuid = provider_uuid
        self._manifest_id = manifest_id
        self.processed_report = processed_report
        self.date_accessor = DateAccessor()
        self._context = {
            "schema": self._schema,
            "report_path": self._report_path,
            "compression": self._compression,
            "provider_uuid": self._provider_uuid,
            "manifest_id": self._manifest_id,
            "data_cutoff_date": self.data_cutoff_date,
        }

    @property
    def data_cutoff_date(self):
        """Determine the date we should use to process and delete data."""
        today = self.date_accessor.today_with_timezone("UTC").date()
        data_cutoff_date = today - relativedelta(days=2)
        if today.month != data_cutoff_date.month:
            data_cutoff_date = today.replace(day=1)
        return data_cutoff_date

    def _get_data_for_table(self, row, table_name):
        """Extract the data from a row for a specific table.

        Args:
            row (dict): A dictionary representation of a CSV file row
            table_name (str): The DB table fields are required for

        Returns:
            (dict): The data from the row keyed on the DB table's column names

        """
        column_map = REPORT_COLUMN_MAP[table_name]
        lower_case_column_map = {key.lower(): value for key, value in column_map.items()}

        result = {
            lower_case_column_map[key.lower()]: value
            for key, value in row.items()
            if key.lower() in lower_case_column_map
        }
        return result

    @staticmethod
    def _get_file_opener(compression):
        """Get the file opener for the file's compression.

        Args:
            compression (str): The compression format for the file.

        Returns:
            (file opener, str): The proper file stream handler for the
                compression and the read mode for the file

        """
        if compression == GZIP_COMPRESSED:
            return gzip.open, "rt"
        return open, "r"  # assume uncompressed by default

    def _write_processed_rows_to_csv(self):
        """Output CSV content to file stream object."""
        values = [tuple(item.values()) for item in self.processed_report.line_items]

        file_obj = io.StringIO()
        writer = csv.writer(file_obj, delimiter=",", quoting=csv.QUOTE_MINIMAL, quotechar='"')
        writer.writerows(values)
        file_obj.seek(0)

        return file_obj

    def _should_process_row(self, row, date_column, is_full_month, is_finalized=None):
        """Determine if we want to process this row.

        Args:
            row (dict): The line item entry from the AWS report file
            date_column (str): The name of date column to check
            is_full_month (boolean): If this is the first time we've processed this bill

        Kwargs:
            is_finalized (boolean): If this is a finalized bill

        Returns:
            (bool): Whether this row should be processed

        """
        if is_finalized or is_full_month:
            return True
        row_date = ciso8601.parse_datetime(row[date_column]).date()
        if row_date < self.data_cutoff_date:
            return False
        return True

    def _should_process_full_month(self):
        """Determine if we should process the full month of data."""
        if not self._manifest_id:
            LOG.info(
                log_json(
                    msg="no manifest provided, processing as a new billing period and entire month",
                    ctx=self._context,
                )
            )
            return True

        with ReportManifestDBAccessor() as manifest_accessor:
            manifest = manifest_accessor.get_manifest_by_id(self._manifest_id)
            bill_date = manifest.billing_period_start_datetime.date()
            provider_uuid = manifest.provider_id

        if bill_date.month != self.data_cutoff_date.month or bill_date.year != self.data_cutoff_date.year:
            LOG.info(
                log_json(
                    msg=f"processing entire month starting on {bill_date}",
                    ctx=self._context,
                    bill_date=bill_date,
                )
            )
            return True

        manifest_list = manifest_accessor.get_manifest_list_for_provider_and_bill_date(provider_uuid, bill_date)

        if len(manifest_list) == 1:
            # This is the first manifest for this bill and we are currently
            # processing it
            LOG.info(
                log_json(
                    msg=f"processing entire month starting on {bill_date}",
                    ctx=self._context,
                    bill_date=bill_date,
                )
            )
            return True

        for manifest in manifest_list:
            with ReportManifestDBAccessor() as manifest_accessor:
                if manifest_accessor.manifest_ready_for_summary(manifest.id):
                    LOG.info(
                        log_json(
                            msg=f"processing bill starting on {bill_date}, on or after {self.data_cutoff_date}",
                            ctx=self._context,
                            bill_date=bill_date,
                        )
                    )
                    # We have fully processed a manifest for this provider
                    return False

        return True

    def get_date_column_filter(self):
        """Return a filter using the provider-appropriate column."""
        result = {"usage_start__gte": self.data_cutoff_date}
        with ProviderDBAccessor(self._provider_uuid) as provider_accessor:
            provider_type = provider_accessor.get_type()
        if provider_type in (Provider.PROVIDER_AZURE, Provider.PROVIDER_AZURE_LOCAL):
            result = {"usage_date__gte": self.data_cutoff_date}
        return result
