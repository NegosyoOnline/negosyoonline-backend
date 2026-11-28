"""The member's media library read, added to the storefront surface next to the member routes
(apps/storefront/api.py mounts this router under /member/):

    POST /storefront/v1/member/media/    {host, token, type?, category?, product_category?,
                                          search?, sort?, offset?}
         -> {items: [asset], count, offset, limit, next_offset | null,
             options: {type: [option], category: [option], product_category: [option]}}

    type: image | video. category: promo | ad | page. product_category: barley | drinks |
    essentials | beauty. search: up to 100 characters, matched in title and caption. sort: newest
    (default) or oldest, by published time. 12 items at a time: send `next_offset` as `offset` for
    the next page. `count` is the number of matches of all the filters. Each option is
    {value, label, count}, counted over every visible asset before any filter.

Same gates as the other member routes: the storefronts server's credential, MEMBERS_ENABLED, a
served Santé host (404) and a live member session of that host's site (401
member_session_invalid). Only active, published assets of the site's template are listed. A
filter value outside the lists above is 400 invalid_request with `fields`.
"""
from datetime import datetime

from ninja import Router, Schema
from pydantic import model_serializer

from apps.members.api import _session, _site

from . import services

router = Router()


class MediaQuery(Schema):
    host: str = ""
    token: str = ""
    type: str | None = None
    category: str | None = None
    product_category: str | None = None
    search: str | None = None
    sort: str | None = None
    offset: int = 0


class AssetOut(Schema):
    id: str
    title: str
    asset_type: str
    category: str
    category_label: str
    product_category: str | None
    product_category_label: str | None
    crop_ratio: str
    caption: str
    file_url: str | None
    embed_url: str | None = None  # a video in the store's own Bunny library: the player URL (no file_url)
    thumb_url: str | None
    poster_url: str | None
    width: int
    height: int
    file_size: int
    duration_seconds: int
    published_at: datetime
    is_new: bool

    @model_serializer(mode="wrap")
    def _no_embed_url_unless_bunny(self, handler):
        """A Blob-hosted asset keeps the shape it always had: no `embed_url` key."""
        data = handler(self)
        if data.get("embed_url") is None:
            data.pop("embed_url", None)
        return data


class OptionOut(Schema):
    value: str
    label: str
    count: int


class OptionsOut(Schema):
    type: list[OptionOut]
    category: list[OptionOut]
    product_category: list[OptionOut]


class MediaOut(Schema):
    items: list[AssetOut]
    count: int
    offset: int
    limit: int
    next_offset: int | None
    options: OptionsOut


@router.post("/media/", response=MediaOut)
def media_list(request, body: MediaQuery):
    member = _session(_site(body.host), body.token).member
    params = body.dict(exclude={"host", "token"})
    return services.list_assets(member, params)
