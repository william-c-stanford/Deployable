export interface KPISubItem {
  label: string;
  value: number;
  color: string;
}

export interface KPICard {
  id: string;
  label: string;
  value: number | string;
  change?: number;
  change_label?: string;
  icon: string;
  color: string;
  link: string;
  sub_items?: KPISubItem[];
}

export interface SuggestedAction {
  id: string;
  action_type: string;
  title: string;
  description?: string;
  link?: string;
  priority: number;
  /** Agent that generated this action */
  agent_name?: string;
  /** Entity context */
  entity_type?: string;
  entity_id?: string;
  /** When the action was created */
  created_at?: string;
  /** How many items are involved (e.g. "3 technicians") */
  count?: number;
  /** Additional metadata for rich display */
  metadata?: Record<string, unknown>;
}

export interface ActivityEntry {
  id: string;
  action: string;
  entity_type?: string;
  entity_id?: string;
  details?: Record<string, unknown>;
  agent_name?: string;
  created_at?: string;
}

export interface DashboardData {
  kpi_cards: KPICard[];
  suggested_actions: SuggestedAction[];
  recent_activity: ActivityEntry[];
}
