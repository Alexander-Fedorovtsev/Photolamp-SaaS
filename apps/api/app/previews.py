from typing import Literal

from pydantic import BaseModel, ConfigDict


class PreviewTenant(BaseModel):
    model_config = ConfigDict(frozen=True)

    slug: str
    display_name: str
    city: str
    timezone: str
    accent: Literal["sun", "mint"]
    is_demo: Literal[True] = True
    booking_enabled: Literal[False] = False
    phone: None = None
    tariff: None = None


# Synthetic public brand fixtures, not tenant records, customer data or live offers.
DEMO_TENANTS: dict[str, PreviewTenant] = {
    "demo-ufa": PreviewTenant(
        slug="demo-ufa",
        display_name="Солнечный дом · демо",
        city="Уфа",
        timezone="Asia/Yekaterinburg",
        accent="sun",
    ),
    "demo-kazan": PreviewTenant(
        slug="demo-kazan",
        display_name="Бережный свет · демо",
        city="Казань",
        timezone="Europe/Moscow",
        accent="mint",
    ),
}
