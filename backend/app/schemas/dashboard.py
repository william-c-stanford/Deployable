from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class KPICard(BaseModel):
    id: str
    label: str
    value: int | float | str
    change: Optional[float] = None  # percentage change
    change_label: Optional[str] = None
    icon: str  # icon name for frontend
    color: str  # tailwind color class
    link: str  # navigation path when clicked
    sub_items: Optional[List[dict]] = None


class SuggestedActionItem(BaseModel):
    id: str
    action_type: str
    title: str
    description: Optional[str] = None
    link: Optional[str] = None
    priority: int = 0
    agent_name: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    created_at: Optional[str] = None
    count: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


class DashboardResponse(BaseModel):
    kpi_cards: List[KPICard]
    suggested_actions: List[SuggestedActionItem]
    recent_activity: List[dict]
