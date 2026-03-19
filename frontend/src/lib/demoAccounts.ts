/**
 * Demo accounts for the role switcher.
 * Each account has an archetype label to help differentiate personas.
 */

export type RoleType = 'ops' | 'technician' | 'partner'

export interface DemoAccount {
  id: string
  name: string
  role: RoleType
  archetype: string
  scoped_to?: string
  /** Brief description shown in the switcher */
  description: string
  /** Initials for avatar */
  initials: string
}

export const roleTypeLabels: Record<RoleType, string> = {
  ops: 'Operations',
  technician: 'Technician',
  partner: 'Partner',
}

export const roleTypeDescriptions: Record<RoleType, string> = {
  ops: 'Manage workforce, projects & agents',
  technician: 'View portal, training & timesheets',
  partner: 'Review assignments & confirmations',
}

export const roleTypeIcons: Record<RoleType, string> = {
  ops: 'shield',
  technician: 'hard-hat',
  partner: 'building',
}

export const demoAccounts: DemoAccount[] = [
  // Ops accounts
  {
    id: 'ops-admin',
    name: 'Jordan Rivera',
    role: 'ops',
    archetype: 'Ops Admin',
    description: 'Full system access, manages all workforce operations',
    initials: 'JR',
  },
  {
    id: 'ops-recruiter',
    name: 'Casey Morgan',
    role: 'ops',
    archetype: 'Recruiter',
    description: 'Focuses on sourcing and training pipeline',
    initials: 'CM',
  },
  {
    id: 'ops-staffing',
    name: 'Alex Chen',
    role: 'ops',
    archetype: 'Staffing Manager',
    description: 'Handles project staffing and assignments',
    initials: 'AC',
  },

  // Technician accounts — diverse archetypes across career stages
  {
    id: 'tech_034',
    name: 'Marcus Johnson',
    role: 'technician',
    archetype: 'Deployed · Lead Splicer',
    description: 'Veteran splicer, currently on Metro Fiber Phoenix',
    initials: 'MJ',
  },
  {
    id: 'tech_012',
    name: 'Elena Martinez',
    role: 'technician',
    archetype: 'In Training · Fiber Splicing',
    description: 'Mid-training, working toward intermediate certs',
    initials: 'EM',
  },
  {
    id: 'tech_027',
    name: 'DeShawn Williams',
    role: 'technician',
    archetype: 'Awaiting Assignment',
    description: 'Training complete, ready for first deployment',
    initials: 'DW',
  },
  {
    id: 'tech_003',
    name: 'Sarah Chen',
    role: 'technician',
    archetype: 'Sourced · New Recruit',
    description: 'Recently sourced, pending screening',
    initials: 'SC',
  },
  {
    id: 'tech_041',
    name: 'Tyler Jackson',
    role: 'technician',
    archetype: 'Deployed · FTTH Installer',
    description: 'Active on FTTH Rollout Charlotte project',
    initials: 'TJ',
  },

  // Partner accounts
  {
    id: 'partner_lumen',
    name: 'Lumen Technologies',
    role: 'partner',
    archetype: 'Tier 1 Carrier',
    scoped_to: 'partner_lumen',
    description: '2 active projects, 8 assigned technicians',
    initials: 'LT',
  },
  {
    id: 'partner_equinix',
    name: 'Equinix',
    role: 'partner',
    archetype: 'Data Center Provider',
    scoped_to: 'partner_equinix',
    description: '1 project in staffing phase',
    initials: 'EQ',
  },
  {
    id: 'partner_att',
    name: 'AT&T',
    role: 'partner',
    archetype: 'National Carrier',
    scoped_to: 'partner_att',
    description: '1 active FTTH rollout project',
    initials: 'AT',
  },
  {
    id: 'partner_crown',
    name: 'Crown Castle',
    role: 'partner',
    archetype: 'Tower / Small Cell',
    scoped_to: 'partner_crown',
    description: '1 project in staffing phase',
    initials: 'CC',
  },
  {
    id: 'partner_zayo',
    name: 'Zayo Group',
    role: 'partner',
    archetype: 'Fiber Network Operator',
    scoped_to: 'partner_zayo',
    description: '1 active WAN upgrade project',
    initials: 'ZG',
  },
]

export function getAccountsByRole(role: RoleType): DemoAccount[] {
  return demoAccounts.filter((a) => a.role === role)
}

export function getDefaultAccount(role: RoleType): DemoAccount {
  return getAccountsByRole(role)[0]
}

export function getAccountById(id: string): DemoAccount | undefined {
  return demoAccounts.find((a) => a.id === id)
}
