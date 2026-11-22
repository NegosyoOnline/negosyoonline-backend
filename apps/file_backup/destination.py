"""The storage bucket (S3-compatible) as the backup writes it.

A small interface the service codes against: put, get, delete, list. boto3 is imported only
when a client is built, so nothing loads it (or calls the network) while the switch is off.
Credentials come from the FILE_BACKUP_* settings and appear in no log line or error.
"""
import logging

logger = logging.getLogger(__name__)


class DestinationError(Exception):
    """The bucket could not do what was asked. The message never holds a key or a name."""


class S3Destination:
    def __init__(self, *, bucket, endpoint, region, access_key_id, secret_access_key):
        if not all((bucket, endpoint, access_key_id, secret_access_key)):
            raise DestinationError("The backup bucket is not fully configured.")
        import boto3
        from botocore.config import Config

        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region or "auto",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                retries={"max_attempts": 5, "mode": "standard"},
                connect_timeout=10,
                read_timeout=60,
                # S3-compatible stores that predate the newer default checksums reject them.
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def _call(self, action, function, **kwargs):
        try:
            return function(Bucket=self._bucket, **kwargs)
        except Exception as error:  # noqa: BLE001 - boto3 raises many types; none is shown
            logger.warning("File backup bucket %s failed: %s", action, type(error).__name__)
            raise DestinationError(f"The backup bucket {action} failed.") from None

    def put(self, key, data):
        self._call("put", self._client.put_object, Key=key, Body=data)

    def get(self, key):
        return self._call("get", self._client.get_object, Key=key)["Body"].read()

    def delete(self, key):
        self._call("delete", self._client.delete_object, Key=key)

    def list(self, prefix):
        """(key, size) of every object under `prefix`."""
        token = None
        while True:
            kwargs = {"Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            answer = self._call("list", self._client.list_objects_v2, **kwargs)
            for item in answer.get("Contents", []):
                yield item["Key"], item["Size"]
            if not answer.get("IsTruncated"):
                return
            token = answer["NextContinuationToken"]
