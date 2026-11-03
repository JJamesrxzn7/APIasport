from app.routers.products import get_product


PRODUCT = {
    "productId": "WEB0000001",
    "id": "futbol-balon-pro-match",
    "categoryId": "futbol",
    "categoryLabel": "Futbol",
    "name": "Balon Pro Match",
    "description": "Balon de futbol",
    "price": "34.90",
    "stock": 8,
    "imageUrl": "https://example.com/cover.svg",
    "brand": "Apex",
}


class Result:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def mappings(self) -> "Result":
        return self

    def one_or_none(self) -> dict | None:
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class ProductDatabase:
    def __init__(self, photos: list[dict]) -> None:
        self.photos = photos

    def execute(self, query: object, params: dict) -> Result:
        if "FROM producto_fotografias" in str(query):
            return Result(self.photos)
        return Result([PRODUCT])


def test_product_detail_returns_ordered_gallery_with_cover_first() -> None:
    db = ProductDatabase(
        [
            {"url": "https://example.com/side.svg"},
            {"url": "https://example.com/cover.svg"},
            {"url": "https://example.com/back.svg"},
        ]
    )

    result = get_product("futbol-balon-pro-match", db)

    assert result["images"] == [
        {"url": "https://example.com/cover.svg", "order": 1},
        {"url": "https://example.com/side.svg", "order": 2},
        {"url": "https://example.com/back.svg", "order": 3},
    ]


def test_product_detail_falls_back_to_cover_when_no_photos_exist() -> None:
    result = get_product("futbol-balon-pro-match", ProductDatabase([]))

    assert result["images"] == [
        {"url": "https://example.com/cover.svg", "order": 1}
    ]
