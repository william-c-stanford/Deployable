import { useState, useEffect, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { NativeSelect as Select } from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Progress } from '@/components/ui/progress'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table'
import { cn, formatDate, getStatusColor, getProgressColor } from '@/lib/utils'
import { useProjectStore } from '@/stores/projectStore'
import api from '@/lib/api'
import { mockProjects } from '@/lib/mockProjects'
import { ForwardStaffingTimeline } from '@/components/staffing/ForwardStaffingTimeline'
import {
  Search,
  MapPin,
  Calendar,
  Users,
  ChevronRight,
  ArrowLeft,
  Building2,
  AlertTriangle,
  Clock,
  CheckCircle2,
  XCircle,
  UserPlus,
  FileText,
  TrendingUp,
  Filter,
  Eye,
  RotateCcw,
  UserX,
  ShieldCheck,
  MessageSquare,
  ArrowRightLeft,
  Timer,
  Flag,
  Scale,
  GanttChart,
  List,
  Lock,
} from 'lucide-react'
import { DisputeSection } from '@/components/project/DisputeSection'
import { ProjectCloseDialog } from '@/components/project/ProjectCloseDialog'
import type { Project, ProjectRole, Assignment, Timesheet, Escalation, EscalationResolution, ReassignmentCandidate } from '@/types'

// --- Project List Mode ---
function ProjectListView({ onSelectProject }: { onSelectProject: (id: string) => void }) {
  const [viewMode, setViewMode] = useState<'list' | 'timeline'>('list')
  const { filters, setFilters, getFilteredProjects, setProjects } = useProjectStore()

  useEffect(() => {
    let cancelled = false
    api.get('/projects')
      .then((res) => {
        if (cancelled) return
        const apiProjects = (res.data.items || []).map((p: any) => ({
          ...p,
          // Map API role format → frontend role format
          roles: (p.roles || []).map((r: any) => ({
            ...r,
            skill_bundle: (r.required_skills || []).map((s: any) => ({
              skill_name: s.skill_name || s.skill || '',
              min_proficiency: s.min_proficiency || s.min_level || 'Beginner',
            })),
          })),
          // Provide empty defaults for nested collections not yet returned by API
          assignments: p.assignments || [],
          timesheets: p.timesheets || [],
          escalations: p.escalations || [],
        }))
        setProjects(apiProjects.length > 0 ? apiProjects : mockProjects)
      })
      .catch(() => {
        // Fallback to mock data if API is unavailable
        if (!cancelled) setProjects(mockProjects)
      })
    return () => { cancelled = true }
  }, [setProjects])

  const { projects } = useProjectStore()
  const filteredProjects = getFilteredProjects()

  const regions = useMemo(
    () => [...new Set(projects.map((p) => p.location_region))].sort(),
    [projects]
  )
  const partners = useMemo(
    () => [...new Map(projects.map((p) => [p.partner_id, p.partner_name])).entries()],
    [projects]
  )
  const statuses: Project['status'][] = ['Draft', 'Staffing', 'Active', 'Wrapping Up', 'Closed']

  const totalProjects = projects.length
  const activeProjects = projects.filter((p) => p.status === 'Active').length
  const staffingProjects = projects.filter((p) => p.status === 'Staffing').length
  const openRoles = projects.reduce(
    (sum, p) => sum + p.roles.reduce((r, role) => r + (role.quantity - role.filled), 0),
    0
  )

  return (
    <div className="space-y-6">
      {/* KPI Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card className="cursor-pointer hover:border-primary/50 transition-colors" onClick={() => setFilters({ status: '' })}>
          <CardContent className="p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">Total Projects</p>
                <p className="text-2xl font-bold">{totalProjects}</p>
              </div>
              <Building2 className="h-8 w-8 text-muted-foreground" />
            </div>
          </CardContent>
        </Card>
        <Card className="cursor-pointer hover:border-green-500/50 transition-colors" onClick={() => setFilters({ status: 'Active' })}>
          <CardContent className="p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">Active</p>
                <p className="text-2xl font-bold text-green-400">{activeProjects}</p>
              </div>
              <TrendingUp className="h-8 w-8 text-green-400" />
            </div>
          </CardContent>
        </Card>
        <Card className="cursor-pointer hover:border-blue-500/50 transition-colors" onClick={() => setFilters({ status: 'Staffing' })}>
          <CardContent className="p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">Staffing</p>
                <p className="text-2xl font-bold text-blue-400">{staffingProjects}</p>
              </div>
              <UserPlus className="h-8 w-8 text-blue-400" />
            </div>
          </CardContent>
        </Card>
        <Card className="cursor-pointer hover:border-amber-500/50 transition-colors">
          <CardContent className="p-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-muted-foreground">Open Roles</p>
                <p className="text-2xl font-bold text-amber-400">{openRoles}</p>
              </div>
              <Users className="h-8 w-8 text-amber-400" />
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="p-4">
          <div className="flex flex-col md:flex-row gap-3">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search projects, partners, locations..."
                className="pl-9"
                value={filters.search}
                onChange={(e) => setFilters({ search: e.target.value })}
              />
            </div>
            <Select
              value={filters.status}
              onChange={(e) => setFilters({ status: e.target.value })}
              className="md:w-40"
            >
              <option value="">All Statuses</option>
              {statuses.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </Select>
            <Select
              value={filters.region}
              onChange={(e) => setFilters({ region: e.target.value })}
              className="md:w-44"
            >
              <option value="">All Regions</option>
              {regions.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </Select>
            <Select
              value={filters.partner}
              onChange={(e) => setFilters({ partner: e.target.value })}
              className="md:w-44"
            >
              <option value="">All Partners</option>
              {partners.map(([id, name]) => (
                <option key={id} value={id}>{name}</option>
              ))}
            </Select>
            {(filters.search || filters.status || filters.region || filters.partner) && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setFilters({ search: '', status: '', region: '', partner: '' })}
                className="shrink-0"
              >
                Clear
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {/* View Toggle */}
      <div className="flex items-center justify-between">
        <div className="flex items-center rounded-md border border-border/50 overflow-hidden">
          <button
            className={cn(
              'flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors',
              viewMode === 'list' ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
            )}
            onClick={() => setViewMode('list')}
          >
            <List className="h-3.5 w-3.5" />
            List
          </button>
          <button
            className={cn(
              'flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors border-l border-border/50',
              viewMode === 'timeline' ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'
            )}
            onClick={() => setViewMode('timeline')}
          >
            <GanttChart className="h-3.5 w-3.5" />
            Timeline
          </button>
        </div>
        <span className="text-xs text-muted-foreground">
          {filteredProjects.length} project{filteredProjects.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Project List or Timeline */}
      {viewMode === 'timeline' ? (
        <ForwardStaffingTimeline
          projects={filteredProjects}
          title="Forward Staffing Timeline"
          description="Cross-project view of technician assignments, gaps, and transitions over the next 90 days"
        />
      ) : (
        <div className="space-y-3">
          {filteredProjects.length === 0 ? (
            <Card>
              <CardContent className="p-12 text-center">
                <Filter className="h-12 w-12 text-muted-foreground mx-auto mb-4" />
                <p className="text-lg font-medium text-muted-foreground">No projects match your filters</p>
                <p className="text-sm text-muted-foreground mt-1">Try adjusting your search criteria</p>
              </CardContent>
            </Card>
          ) : (
            filteredProjects.map((project) => (
              <ProjectListCard
                key={project.id}
                project={project}
                onClick={() => onSelectProject(project.id)}
              />
            ))
          )}
        </div>
      )}
    </div>
  )
}

function ProjectListCard({ project, onClick }: { project: Project; onClick: () => void }) {
  const { getProjectDisputes } = useProjectStore()
  const totalRoles = project.roles.reduce((s, r) => s + r.quantity, 0)
  const filledRoles = project.roles.reduce((s, r) => s + r.filled, 0)
  const fillPercent = totalRoles > 0 ? Math.round((filledRoles / totalRoles) * 100) : 0
  const openEscalations = (project.escalations || []).filter((e) => e.status === 'Open').length
  const pendingTimesheets = (project.timesheets || []).filter(
    (t) => t.status === 'Submitted' || t.status === 'Flagged'
  ).length
  const activeDisputes = getProjectDisputes(project.id).filter(
    (d) => !d.dispute_status.startsWith('resolved')
  ).length

  return (
    <Card
      className="cursor-pointer hover:border-primary/40 transition-all hover:shadow-md group"
      onClick={onClick}
    >
      <CardContent className="p-5">
        <div className="flex flex-col md:flex-row md:items-center gap-4">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 mb-2">
              <h3 className="font-semibold text-lg truncate group-hover:text-primary transition-colors">
                {project.name}
              </h3>
              <Badge className={getStatusColor(project.status)}>{project.status}</Badge>
            </div>
            <div className="flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <Building2 className="h-3.5 w-3.5" />
                {project.partner_name}
              </span>
              <span className="flex items-center gap-1.5">
                <MapPin className="h-3.5 w-3.5" />
                {project.location_city ? `${project.location_city}, ` : ''}{project.location_region}
              </span>
              <span className="flex items-center gap-1.5">
                <Calendar className="h-3.5 w-3.5" />
                {formatDate(project.start_date)}
                {project.end_date ? ` - ${formatDate(project.end_date)}` : ''}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-6 md:gap-8">
            <div className="w-32">
              <div className="flex justify-between text-xs mb-1">
                <span className="text-muted-foreground">Staffing</span>
                <span className="font-medium">{filledRoles}/{totalRoles}</span>
              </div>
              <Progress
                value={fillPercent}
                indicatorClassName={getProgressColor(fillPercent)}
              />
            </div>
            <div className="flex items-center gap-2">
              {openEscalations > 0 && (
                <span className="flex items-center gap-1 text-xs bg-red-500/20 text-red-400 rounded-full px-2 py-1">
                  <AlertTriangle className="h-3 w-3" />
                  {openEscalations}
                </span>
              )}
              {activeDisputes > 0 && (
                <span className="flex items-center gap-1 text-xs bg-orange-500/20 text-orange-400 rounded-full px-2 py-1">
                  <Flag className="h-3 w-3" />
                  {activeDisputes}
                </span>
              )}
              {pendingTimesheets > 0 && (
                <span className="flex items-center gap-1 text-xs bg-amber-500/20 text-amber-400 rounded-full px-2 py-1">
                  <Clock className="h-3 w-3" />
                  {pendingTimesheets}
                </span>
              )}
            </div>
            <ChevronRight className="h-5 w-5 text-muted-foreground group-hover:text-primary transition-colors" />
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

// --- Project Detail Mode ---
function ProjectDetailView({
  project,
  onBack,
}: {
  project: Project
  onBack: () => void
}) {
  const [activeTab, setActiveTab] = useState('overview')
  const [closeDialogOpen, setCloseDialogOpen] = useState(false)
  const { updateAssignment, updateTimesheet, closeProject } = useProjectStore()

  const totalRoles = project.roles.reduce((s, r) => s + r.quantity, 0)
  const filledRoles = project.roles.reduce((s, r) => s + r.filled, 0)
  const fillPercent = totalRoles > 0 ? Math.round((filledRoles / totalRoles) * 100) : 0

  const handleConfirmClose = () => {
    const success = closeProject(project.id)
    if (success) {
      setCloseDialogOpen(false)
    }
  }

  const canShowCloseButton = project.status !== 'Closed' && project.status !== 'Draft'

  return (
    <div className="space-y-6">
      {/* Project Close Dialog */}
      <ProjectCloseDialog
        project={project}
        open={closeDialogOpen}
        onOpenChange={setCloseDialogOpen}
        onNavigateTab={setActiveTab}
        onConfirmClose={handleConfirmClose}
      />

      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-start gap-4">
        <Button variant="ghost" size="icon" onClick={onBack} className="shrink-0 -ml-2">
          <ArrowLeft className="h-5 w-5" />
        </Button>
        <div className="flex-1">
          <div className="flex items-center gap-3 mb-2">
            <h1 className="text-2xl font-bold">{project.name}</h1>
            <Badge className={cn(getStatusColor(project.status), 'text-sm')}>{project.status}</Badge>
            {canShowCloseButton && (
              <Button
                variant="outline"
                size="sm"
                className="ml-auto border-red-500/30 text-red-400 hover:bg-red-500/10 hover:text-red-300"
                onClick={() => setCloseDialogOpen(true)}
              >
                <Lock className="h-3.5 w-3.5 mr-1.5" />
                Close Project
              </Button>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <Building2 className="h-4 w-4" />
              {project.partner_name}
            </span>
            <span className="flex items-center gap-1.5">
              <MapPin className="h-4 w-4" />
              {project.location_city ? `${project.location_city}, ` : ''}{project.location_region}
            </span>
            <span className="flex items-center gap-1.5">
              <Calendar className="h-4 w-4" />
              {formatDate(project.start_date)} - {project.end_date ? formatDate(project.end_date) : 'TBD'}
            </span>
            <span className="flex items-center gap-1.5">
              <Users className="h-4 w-4" />
              {filledRoles}/{totalRoles} roles filled ({fillPercent}%)
            </span>
          </div>
          {project.description && (
            <p className="text-sm text-muted-foreground mt-3 max-w-3xl">{project.description}</p>
          )}
        </div>
      </div>

      {/* Tabbed Layout */}
      <Tabs defaultValue="overview" value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="w-full md:w-auto flex-wrap">
          <TabsTrigger value="overview">
            <Eye className="h-4 w-4 mr-1.5" />
            Overview
          </TabsTrigger>
          <TabsTrigger value="roles">
            <Users className="h-4 w-4 mr-1.5" />
            Roles & Staffing
          </TabsTrigger>
          <TabsTrigger value="assignments">
            <UserPlus className="h-4 w-4 mr-1.5" />
            Assignments
            {project.assignments.length > 0 && (
              <Badge className="ml-1.5 bg-primary/20 text-primary text-[10px] px-1.5 py-0 border-0">
                {project.assignments.length}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="timesheets">
            <FileText className="h-4 w-4 mr-1.5" />
            Timesheets
            {(project.timesheets || []).filter((t) => t.status === 'Flagged').length > 0 && (
              <Badge className="ml-1.5 bg-red-500/20 text-red-400 text-[10px] px-1.5 py-0 border-0">
                {(project.timesheets || []).filter((t) => t.status === 'Flagged').length}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="disputes">
            <Scale className="h-4 w-4 mr-1.5" />
            Disputes
            {useProjectStore.getState().getProjectDisputes(project.id).filter((d) => !d.dispute_status.startsWith('resolved')).length > 0 && (
              <Badge className="ml-1.5 bg-red-500/20 text-red-400 text-[10px] px-1.5 py-0 border-0">
                {useProjectStore.getState().getProjectDisputes(project.id).filter((d) => !d.dispute_status.startsWith('resolved')).length}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="escalations">
            <AlertTriangle className="h-4 w-4 mr-1.5" />
            Escalations
            {(project.escalations || []).filter((e) => e.status === 'Open').length > 0 && (
              <Badge className="ml-1.5 bg-red-500/20 text-red-400 text-[10px] px-1.5 py-0 border-0">
                {(project.escalations || []).filter((e) => e.status === 'Open').length}
              </Badge>
            )}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <OverviewTab project={project} />
        </TabsContent>
        <TabsContent value="roles">
          <RolesTab project={project} />
        </TabsContent>
        <TabsContent value="assignments">
          <AssignmentsTab
            project={project}
            onUpdateAssignment={(assignmentId, updates) =>
              updateAssignment(project.id, assignmentId, updates)
            }
          />
        </TabsContent>
        <TabsContent value="timesheets">
          <TimesheetsTab
            project={project}
            onUpdateTimesheet={(timesheetId, updates) =>
              updateTimesheet(project.id, timesheetId, updates)
            }
          />
        </TabsContent>
        <TabsContent value="disputes">
          <div className="mt-4">
            <DisputeSection projectId={project.id} />
          </div>
        </TabsContent>
        <TabsContent value="escalations">
          <EscalationsTab project={project} />
        </TabsContent>
      </Tabs>
    </div>
  )
}

// --- Overview Tab ---
function OverviewTab({ project }: { project: Project }) {
  const { getProjectDisputes } = useProjectStore()
  const totalRoles = project.roles.reduce((s, r) => s + r.quantity, 0)
  const filledRoles = project.roles.reduce((s, r) => s + r.filled, 0)
  const openEscalations = (project.escalations || []).filter((e) => e.status === 'Open').length
  const pendingTimesheets = (project.timesheets || []).filter((t) => t.status === 'Submitted').length
  const flaggedTimesheets = (project.timesheets || []).filter((t) => t.status === 'Flagged').length
  const activeAssignments = project.assignments.filter((a) => a.status === 'Active').length
  const pendingConfirmations = project.assignments.filter((a) => a.status === 'Pending Confirmation').length
  const activeDisputes = getProjectDisputes(project.id).filter((d) => !d.dispute_status.startsWith('resolved')).length

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mt-4">
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Staffing Summary</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {project.roles.map((role) => (
              <div key={role.id}>
                <div className="flex justify-between text-sm mb-1">
                  <span>{role.role_name}</span>
                  <span className="font-medium">{role.filled}/{role.quantity}</span>
                </div>
                <Progress
                  value={(role.filled / role.quantity) * 100}
                  indicatorClassName={getProgressColor((role.filled / role.quantity) * 100)}
                />
              </div>
            ))}
            <div className="pt-2 border-t">
              <div className="flex justify-between text-sm font-medium">
                <span>Total</span>
                <span>{filledRoles}/{totalRoles} ({totalRoles > 0 ? Math.round((filledRoles / totalRoles) * 100) : 0}%)</span>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Status Overview</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            <div className="flex items-center justify-between p-2 rounded-md bg-muted/50">
              <span className="text-sm flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4 text-green-400" />
                Active Assignments
              </span>
              <span className="font-semibold">{activeAssignments}</span>
            </div>
            <div className="flex items-center justify-between p-2 rounded-md bg-muted/50">
              <span className="text-sm flex items-center gap-2">
                <Clock className="h-4 w-4 text-amber-400" />
                Pending Confirmations
              </span>
              <span className="font-semibold">{pendingConfirmations}</span>
            </div>
            <div className="flex items-center justify-between p-2 rounded-md bg-muted/50">
              <span className="text-sm flex items-center gap-2">
                <FileText className="h-4 w-4 text-blue-400" />
                Pending Timesheets
              </span>
              <span className="font-semibold">{pendingTimesheets}</span>
            </div>
            <div className="flex items-center justify-between p-2 rounded-md bg-muted/50">
              <span className="text-sm flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-red-400" />
                Flagged Timesheets
              </span>
              <span className="font-semibold text-red-400">{flaggedTimesheets}</span>
            </div>
            <div className="flex items-center justify-between p-2 rounded-md bg-muted/50">
              <span className="text-sm flex items-center gap-2">
                <Flag className="h-4 w-4 text-orange-400" />
                Partner Disputes
              </span>
              <span className={`font-semibold ${activeDisputes > 0 ? 'text-orange-400' : ''}`}>{activeDisputes}</span>
            </div>
            <div className="flex items-center justify-between p-2 rounded-md bg-muted/50">
              <span className="text-sm flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-red-400" />
                Open Escalations
              </span>
              <span className="font-semibold text-red-400">{openEscalations}</span>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Project Details</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            <div>
              <span className="text-xs text-muted-foreground uppercase tracking-wider">Partner</span>
              <p className="text-sm font-medium mt-0.5">{project.partner_name}</p>
            </div>
            <div>
              <span className="text-xs text-muted-foreground uppercase tracking-wider">Location</span>
              <p className="text-sm font-medium mt-0.5">
                {project.location_city ? `${project.location_city}, ` : ''}{project.location_region}
              </p>
            </div>
            <div>
              <span className="text-xs text-muted-foreground uppercase tracking-wider">Timeline</span>
              <p className="text-sm font-medium mt-0.5">
                {formatDate(project.start_date)} - {project.end_date ? formatDate(project.end_date) : 'TBD'}
              </p>
            </div>
            <div>
              <span className="text-xs text-muted-foreground uppercase tracking-wider">Total Roles</span>
              <p className="text-sm font-medium mt-0.5">
                {project.roles.length} role types, {totalRoles} positions
              </p>
            </div>
            <div>
              <span className="text-xs text-muted-foreground uppercase tracking-wider">Active Techs</span>
              <p className="text-sm font-medium mt-0.5">
                {project.assignments.filter((a) => a.status === 'Active').length} technicians on-site
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

// --- Roles & Staffing Tab ---
function RolesTab({ project }: { project: Project }) {
  return (
    <div className="space-y-4 mt-4">
      {project.roles.map((role) => (
        <RoleCard key={role.id} role={role} assignments={project.assignments} />
      ))}
    </div>
  )
}

function RoleCard({ role, assignments }: { role: ProjectRole; assignments: Assignment[] }) {
  const roleAssignments = assignments.filter((a) => a.role_id === role.id)
  const unfilled = role.quantity - role.filled
  const fillPercent = (role.filled / role.quantity) * 100

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-2">
          <div>
            <CardTitle className="text-base">{role.role_name}</CardTitle>
            <CardDescription>
              {role.filled}/{role.quantity} filled
              {unfilled > 0 && (
                <span className="text-amber-400 ml-2">({unfilled} open)</span>
              )}
            </CardDescription>
          </div>
          <div className="flex items-center gap-3">
            {role.hourly_rate && (
              <span className="text-sm text-muted-foreground">
                ${role.hourly_rate}/hr
                {role.per_diem ? ` + $${role.per_diem}/day per diem` : ''}
              </span>
            )}
            {unfilled > 0 && (
              <Button size="sm" variant="outline">
                <UserPlus className="h-3.5 w-3.5 mr-1.5" />
                Find Candidates
              </Button>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="mb-4">
          <Progress value={fillPercent} indicatorClassName={getProgressColor(fillPercent)} />
        </div>

        <div className="mb-4">
          <p className="text-xs text-muted-foreground uppercase tracking-wider mb-2">Required Skills</p>
          <div className="flex flex-wrap gap-2">
            {role.skill_bundle.map((skill) => (
              <Badge key={skill.skill_name} variant="outline" className="text-xs">
                {skill.skill_name} ({skill.min_proficiency})
              </Badge>
            ))}
          </div>
        </div>

        <div className="mb-4">
          <p className="text-xs text-muted-foreground uppercase tracking-wider mb-2">Required Certifications</p>
          <div className="flex flex-wrap gap-2">
            {role.required_certs.map((cert) => (
              <Badge key={cert} variant="secondary" className="text-xs">
                {cert}
              </Badge>
            ))}
          </div>
        </div>

        {roleAssignments.length > 0 && (
          <div>
            <p className="text-xs text-muted-foreground uppercase tracking-wider mb-2">Assigned Technicians</p>
            <div className="space-y-2">
              {roleAssignments.map((assignment) => (
                <div
                  key={assignment.id}
                  className="flex items-center justify-between p-2 rounded-md bg-muted/30"
                >
                  <div className="flex items-center gap-3">
                    <div className="h-8 w-8 rounded-full bg-primary/20 flex items-center justify-center text-xs font-medium text-primary">
                      {assignment.technician_name.split(' ').map((n) => n[0]).join('')}
                    </div>
                    <div>
                      <p className="text-sm font-medium">{assignment.technician_name}</p>
                      <p className="text-xs text-muted-foreground">
                        {formatDate(assignment.start_date)} - {formatDate(assignment.end_date)}
                      </p>
                    </div>
                  </div>
                  <Badge className={getStatusColor(assignment.status)}>{assignment.status}</Badge>
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// --- Assignments Tab ---
function AssignmentsTab({
  project,
  onUpdateAssignment,
}: {
  project: Project
  onUpdateAssignment: (assignmentId: string, updates: Partial<Assignment>) => void
}) {
  const [filter, setFilter] = useState<string>('')

  const filteredAssignments = filter
    ? project.assignments.filter((a) => a.status === filter)
    : project.assignments

  const statusCounts = {
    all: project.assignments.length,
    active: project.assignments.filter((a) => a.status === 'Active').length,
    pending: project.assignments.filter((a) => a.status === 'Pending Confirmation').length,
    completed: project.assignments.filter((a) => a.status === 'Completed').length,
  }

  return (
    <div className="space-y-4 mt-4">
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant={filter === '' ? 'default' : 'outline'} onClick={() => setFilter('')}>
          All ({statusCounts.all})
        </Button>
        <Button size="sm" variant={filter === 'Active' ? 'default' : 'outline'} onClick={() => setFilter('Active')}>
          Active ({statusCounts.active})
        </Button>
        <Button
          size="sm"
          variant={filter === 'Pending Confirmation' ? 'default' : 'outline'}
          onClick={() => setFilter('Pending Confirmation')}
          className={statusCounts.pending > 0 ? 'border-amber-500/50' : ''}
        >
          Pending ({statusCounts.pending})
        </Button>
        <Button size="sm" variant={filter === 'Completed' ? 'default' : 'outline'} onClick={() => setFilter('Completed')}>
          Completed ({statusCounts.completed})
        </Button>
      </div>

      {filteredAssignments.length === 0 ? (
        <Card>
          <CardContent className="p-8 text-center">
            <Users className="h-10 w-10 text-muted-foreground mx-auto mb-3" />
            <p className="text-muted-foreground">No assignments found</p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Technician</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Dates</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Rate</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredAssignments.map((assignment) => (
                  <TableRow key={assignment.id}>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <div className="h-8 w-8 rounded-full bg-primary/20 flex items-center justify-center text-xs font-medium text-primary shrink-0">
                          {assignment.technician_name.split(' ').map((n) => n[0]).join('')}
                        </div>
                        <span className="font-medium whitespace-nowrap">{assignment.technician_name}</span>
                      </div>
                    </TableCell>
                    <TableCell>{assignment.role_name}</TableCell>
                    <TableCell className="whitespace-nowrap">
                      <div className="text-sm">
                        {formatDate(assignment.start_date)}
                        <span className="text-muted-foreground mx-1">-</span>
                        {formatDate(assignment.end_date)}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={assignment.assignment_type === 'active' ? 'default' : 'outline'}
                        className="text-xs"
                      >
                        {assignment.assignment_type === 'active' ? 'Active' : 'Pre-booked'}
                      </Badge>
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      ${assignment.hourly_rate}/hr
                      {assignment.per_diem > 0 && (
                        <span className="text-muted-foreground text-xs ml-1">+${assignment.per_diem}/d</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge className={getStatusColor(assignment.status)}>{assignment.status}</Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      {assignment.status === 'Pending Confirmation' && (
                        <div className="flex gap-1 justify-end">
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 text-green-400 hover:text-green-300 hover:bg-green-500/10"
                            onClick={() =>
                              onUpdateAssignment(assignment.id, {
                                status: 'Active',
                                partner_confirmed: true,
                              })
                            }
                          >
                            <CheckCircle2 className="h-3.5 w-3.5 mr-1" />
                            Confirm
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 text-red-400 hover:text-red-300 hover:bg-red-500/10"
                            onClick={() =>
                              onUpdateAssignment(assignment.id, { status: 'Cancelled' })
                            }
                          >
                            <XCircle className="h-3.5 w-3.5 mr-1" />
                            Cancel
                          </Button>
                        </div>
                      )}
                      {assignment.status === 'Active' && (
                        <Button size="sm" variant="ghost" className="h-7">
                          <Eye className="h-3.5 w-3.5 mr-1" />
                          View
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </Card>
      )}
    </div>
  )
}

// --- Timesheets Tab ---
function TimesheetsTab({
  project,
  onUpdateTimesheet,
}: {
  project: Project
  onUpdateTimesheet: (timesheetId: string, updates: Partial<Timesheet>) => void
}) {
  const timesheets = project.timesheets || []
  const [filter, setFilter] = useState<string>('')

  const filteredTimesheets = filter
    ? timesheets.filter((t) => t.status === filter)
    : timesheets

  return (
    <div className="space-y-4 mt-4">
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant={filter === '' ? 'default' : 'outline'} onClick={() => setFilter('')}>
          All ({timesheets.length})
        </Button>
        <Button size="sm" variant={filter === 'Submitted' ? 'default' : 'outline'} onClick={() => setFilter('Submitted')}>
          Submitted ({timesheets.filter((t) => t.status === 'Submitted').length})
        </Button>
        <Button
          size="sm"
          variant={filter === 'Flagged' ? 'default' : 'outline'}
          onClick={() => setFilter('Flagged')}
          className={timesheets.filter((t) => t.status === 'Flagged').length > 0 ? 'border-red-500/50' : ''}
        >
          Flagged ({timesheets.filter((t) => t.status === 'Flagged').length})
        </Button>
        <Button size="sm" variant={filter === 'Approved' ? 'default' : 'outline'} onClick={() => setFilter('Approved')}>
          Approved ({timesheets.filter((t) => t.status === 'Approved').length})
        </Button>
      </div>

      {filteredTimesheets.length === 0 ? (
        <Card>
          <CardContent className="p-8 text-center">
            <FileText className="h-10 w-10 text-muted-foreground mx-auto mb-3" />
            <p className="text-muted-foreground">No timesheets found</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {filteredTimesheets.map((ts) => (
            <Card key={ts.id} className={ts.status === 'Flagged' ? 'border-red-500/30' : ''}>
              <CardContent className="p-4">
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <div className="h-10 w-10 rounded-full bg-primary/20 flex items-center justify-center text-sm font-medium text-primary shrink-0">
                      {ts.technician_name.split(' ').map((n) => n[0]).join('')}
                    </div>
                    <div>
                      <p className="font-medium">{ts.technician_name}</p>
                      <p className="text-sm text-muted-foreground">
                        Week of {formatDate(ts.week_start)} - {formatDate(ts.week_end)}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-4">
                    <div className="text-right">
                      <p className="text-lg font-semibold">{ts.hours}h</p>
                      <p className="text-xs text-muted-foreground">submitted</p>
                    </div>
                    <Badge className={getStatusColor(ts.status)}>{ts.status}</Badge>
                    {(ts.status === 'Submitted' || ts.status === 'Flagged') && (
                      <div className="flex gap-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-8 text-green-400 hover:text-green-300 hover:bg-green-500/10"
                          onClick={() => onUpdateTimesheet(ts.id, { status: 'Approved' })}
                        >
                          <CheckCircle2 className="h-3.5 w-3.5 mr-1" />
                          Approve
                        </Button>
                        {ts.status === 'Submitted' && (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-8 text-amber-400 hover:text-amber-300 hover:bg-amber-500/10"
                            onClick={() =>
                              onUpdateTimesheet(ts.id, {
                                status: 'Flagged',
                                flag_comment: 'Please verify hours',
                              })
                            }
                          >
                            <AlertTriangle className="h-3.5 w-3.5 mr-1" />
                            Flag
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                </div>
                {ts.status === 'Flagged' && ts.flag_comment && (
                  <div className="mt-3 p-3 rounded-md bg-red-500/10 border border-red-500/20">
                    <p className="text-sm text-red-400">
                      <AlertTriangle className="h-3.5 w-3.5 inline mr-1.5" />
                      {ts.flag_comment}
                    </p>
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}

// --- Mock reassignment candidates for demo ---
const MOCK_REASSIGNMENT_CANDIDATES: ReassignmentCandidate[] = [
  {
    technician_id: 'tech-030',
    technician_name: 'Derek Palmer',
    home_base_city: 'Phoenix',
    career_stage: 'Deployed',
    deployability_status: 'Rolling Off Soon',
    available_from: '2026-03-25',
    matching_skills: ['Fiber Splicing', 'Cable Pulling'],
    matching_certs: ['OSHA 10'],
  },
  {
    technician_id: 'tech-032',
    technician_name: 'Vanessa Cruz',
    home_base_city: 'Tucson',
    career_stage: 'Awaiting Assignment',
    deployability_status: 'Ready Now',
    available_from: '2026-03-20',
    matching_skills: ['Fiber Splicing', 'OTDR Testing'],
    matching_certs: ['CFOT', 'OSHA 10'],
  },
  {
    technician_id: 'tech-035',
    technician_name: 'Hassan Ali',
    home_base_city: 'Mesa',
    career_stage: 'Training Completed',
    deployability_status: 'Ready Now',
    available_from: '2026-03-19',
    matching_skills: ['Fiber Splicing'],
    matching_certs: ['OSHA 10'],
  },
  {
    technician_id: 'tech-041',
    technician_name: 'Rachel Kim',
    home_base_city: 'Dallas',
    career_stage: 'Awaiting Assignment',
    deployability_status: 'Ready Now',
    available_from: '2026-03-22',
    matching_skills: ['Structured Cabling', 'Cable Management'],
    matching_certs: ['BICSI RCDD', 'OSHA 30'],
  },
  {
    technician_id: 'tech-044',
    technician_name: 'Brian O\'Neill',
    home_base_city: 'Fort Worth',
    career_stage: 'Deployed',
    deployability_status: 'Rolling Off Soon',
    available_from: '2026-03-28',
    matching_skills: ['Fiber Splicing', 'Cable Pulling'],
    matching_certs: ['CFOT', 'OSHA 10'],
  },
]

// --- Escalation Resolution Panel ---
function EscalationResolvePanel({
  escalation,
  project,
  onResolve,
  onCancel,
}: {
  escalation: Escalation
  project: Project
  onResolve: (resolution: EscalationResolution, newTechName?: string) => void
  onCancel: () => void
}) {
  const [resolution, setResolution] = useState<'confirm' | 'reassign' | 'cancel' | null>(null)
  const [resolutionNote, setResolutionNote] = useState('')
  const [selectedTech, setSelectedTech] = useState<ReassignmentCandidate | null>(null)
  const [newStartDate, setNewStartDate] = useState('')

  const candidates = MOCK_REASSIGNMENT_CANDIDATES

  const handleSubmit = () => {
    if (!resolution) return
    const data: EscalationResolution = {
      resolution,
      resolution_note: resolutionNote || undefined,
      new_technician_id: resolution === 'reassign' ? selectedTech?.technician_id : undefined,
      new_start_date: resolution === 'reassign' && newStartDate ? newStartDate : undefined,
    }
    onResolve(data, selectedTech?.technician_name)
  }

  return (
    <div className="border border-border rounded-lg p-4 mt-3 bg-muted/30 space-y-4">
      <div className="flex items-center justify-between">
        <h4 className="font-medium text-sm">Resolve Escalation</h4>
        <Button variant="ghost" size="sm" onClick={onCancel} className="h-7 text-xs">
          Cancel
        </Button>
      </div>

      {/* Resolution type selection */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
        <button
          onClick={() => { setResolution('confirm'); setSelectedTech(null) }}
          className={cn(
            'flex items-center gap-2 p-3 rounded-lg border text-left transition-all text-sm',
            resolution === 'confirm'
              ? 'border-green-500 bg-green-500/10 text-green-400'
              : 'border-border hover:border-green-500/50 text-muted-foreground hover:text-foreground'
          )}
        >
          <ShieldCheck className="h-4 w-4 shrink-0" />
          <div>
            <p className="font-medium">Override & Confirm</p>
            <p className="text-xs opacity-75 mt-0.5">Confirm assignment without partner</p>
          </div>
        </button>
        <button
          onClick={() => setResolution('reassign')}
          className={cn(
            'flex items-center gap-2 p-3 rounded-lg border text-left transition-all text-sm',
            resolution === 'reassign'
              ? 'border-blue-500 bg-blue-500/10 text-blue-400'
              : 'border-border hover:border-blue-500/50 text-muted-foreground hover:text-foreground'
          )}
        >
          <ArrowRightLeft className="h-4 w-4 shrink-0" />
          <div>
            <p className="font-medium">Reassign</p>
            <p className="text-xs opacity-75 mt-0.5">Assign a different technician</p>
          </div>
        </button>
        <button
          onClick={() => { setResolution('cancel'); setSelectedTech(null) }}
          className={cn(
            'flex items-center gap-2 p-3 rounded-lg border text-left transition-all text-sm',
            resolution === 'cancel'
              ? 'border-red-500 bg-red-500/10 text-red-400'
              : 'border-border hover:border-red-500/50 text-muted-foreground hover:text-foreground'
          )}
        >
          <UserX className="h-4 w-4 shrink-0" />
          <div>
            <p className="font-medium">Cancel Assignment</p>
            <p className="text-xs opacity-75 mt-0.5">Cancel and free the role slot</p>
          </div>
        </button>
      </div>

      {/* Reassignment candidate list */}
      {resolution === 'reassign' && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
            Select Replacement Technician
          </p>
          <div className="max-h-60 overflow-y-auto space-y-2 pr-1">
            {candidates.map((c) => (
              <button
                key={c.technician_id}
                onClick={() => setSelectedTech(c)}
                className={cn(
                  'w-full flex items-center gap-3 p-3 rounded-lg border text-left transition-all text-sm',
                  selectedTech?.technician_id === c.technician_id
                    ? 'border-primary bg-primary/10'
                    : 'border-border hover:border-primary/50'
                )}
              >
                <div className="h-9 w-9 rounded-full bg-primary/20 flex items-center justify-center text-xs font-medium text-primary shrink-0">
                  {c.technician_name.split(' ').map((n) => n[0]).join('')}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="font-medium truncate">{c.technician_name}</p>
                    <Badge
                      variant="outline"
                      className={cn(
                        'text-[10px] shrink-0',
                        c.deployability_status === 'Ready Now'
                          ? 'border-green-500/50 text-green-400'
                          : 'border-amber-500/50 text-amber-400'
                      )}
                    >
                      {c.deployability_status}
                    </Badge>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-muted-foreground mt-0.5">
                    <span className="flex items-center gap-1">
                      <MapPin className="h-3 w-3" />
                      {c.home_base_city}
                    </span>
                    <span className="flex items-center gap-1">
                      <Calendar className="h-3 w-3" />
                      Avail {c.available_from}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-1 mt-1.5">
                    {c.matching_skills.map((s) => (
                      <Badge key={s} variant="secondary" className="text-[10px] h-4 px-1.5">
                        {s}
                      </Badge>
                    ))}
                    {c.matching_certs.map((cert) => (
                      <Badge key={cert} className="text-[10px] h-4 px-1.5 bg-blue-500/20 text-blue-400 border-0">
                        {cert}
                      </Badge>
                    ))}
                  </div>
                </div>
              </button>
            ))}
          </div>
          {selectedTech && (
            <div className="flex items-center gap-3 mt-2">
              <label className="text-xs text-muted-foreground whitespace-nowrap">New Start Date:</label>
              <Input
                type="date"
                value={newStartDate}
                onChange={(e) => setNewStartDate(e.target.value)}
                className="h-8 text-sm max-w-[180px]"
              />
            </div>
          )}
        </div>
      )}

      {/* Resolution note */}
      <div>
        <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider block mb-1.5">
          Resolution Note {resolution !== 'confirm' ? '(recommended)' : '(optional)'}
        </label>
        <Input
          placeholder="Add context for this resolution..."
          value={resolutionNote}
          onChange={(e) => setResolutionNote(e.target.value)}
          className="h-9 text-sm"
        />
      </div>

      {/* Submit */}
      <div className="flex items-center justify-end gap-2 pt-1">
        <Button variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          size="sm"
          disabled={!resolution || (resolution === 'reassign' && !selectedTech)}
          onClick={handleSubmit}
          className={cn(
            resolution === 'confirm' && 'bg-green-600 hover:bg-green-700',
            resolution === 'reassign' && 'bg-blue-600 hover:bg-blue-700',
            resolution === 'cancel' && 'bg-red-600 hover:bg-red-700',
          )}
        >
          {resolution === 'confirm' && 'Override & Confirm'}
          {resolution === 'reassign' && (selectedTech ? `Reassign to ${selectedTech.technician_name}` : 'Select a technician')}
          {resolution === 'cancel' && 'Cancel Assignment'}
          {!resolution && 'Select an action'}
        </Button>
      </div>
    </div>
  )
}

// --- Escalations Tab ---
function EscalationsTab({ project }: { project: Project }) {
  const escalations = project.escalations || []
  const openEscalations = escalations.filter((e) => e.status === 'Open')
  const resolvedEscalations = escalations.filter((e) => e.status === 'Resolved')
  const [resolvingId, setResolvingId] = useState<string | null>(null)
  const [showResolved, setShowResolved] = useState(false)
  const { resolveEscalation, acknowledgeEscalation } = useProjectStore()

  const getEscalationIcon = (type: string) => {
    switch (type) {
      case 'confirmation': return <Clock className="h-5 w-5 text-amber-400" />
      case 'timesheet_dispute': return <FileText className="h-5 w-5 text-red-400" />
      case 'assignment_end': return <Users className="h-5 w-5 text-blue-400" />
      default: return <AlertTriangle className="h-5 w-5 text-muted-foreground" />
    }
  }

  const getEscalationLabel = (type: string) => {
    switch (type) {
      case 'confirmation': return 'Confirmation Overdue'
      case 'timesheet_dispute': return 'Timesheet Dispute'
      case 'assignment_end': return 'Assignment End'
      default: return type
    }
  }

  const getEscalationStatusBadge = (esc: Escalation) => {
    if (esc.escalation_status === 'ops_reviewing') {
      return <Badge className="bg-blue-500/20 text-blue-400 border-0 text-[10px]">Ops Reviewing</Badge>
    }
    if (esc.escalation_status === 'resolved_confirmed') {
      return <Badge className="bg-green-500/20 text-green-400 border-0 text-[10px]">Confirmed</Badge>
    }
    if (esc.escalation_status === 'resolved_reassigned') {
      return <Badge className="bg-blue-500/20 text-blue-400 border-0 text-[10px]">Reassigned</Badge>
    }
    if (esc.escalation_status === 'resolved_cancelled') {
      return <Badge className="bg-red-500/20 text-red-400 border-0 text-[10px]">Cancelled</Badge>
    }
    return <Badge className="bg-red-500/20 text-red-400 border-0 text-[10px]">Escalated</Badge>
  }

  const handleResolve = (escalationId: string, resolution: EscalationResolution, newTechName?: string) => {
    resolveEscalation(project.id, escalationId, resolution, newTechName)
    setResolvingId(null)
  }

  return (
    <div className="space-y-4 mt-4">
      {/* Summary Banner */}
      {openEscalations.length > 0 && (
        <Card className="border-red-500/30 bg-red-500/5">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 rounded-full bg-red-500/20 flex items-center justify-center shrink-0">
                <AlertTriangle className="h-5 w-5 text-red-400" />
              </div>
              <div className="flex-1">
                <p className="font-semibold text-red-400">
                  {openEscalations.length} Open Escalation{openEscalations.length > 1 ? 's' : ''} Require Action
                </p>
                <p className="text-sm text-muted-foreground mt-0.5">
                  {openEscalations.filter((e) => e.type === 'confirmation').length} unconfirmed assignment{openEscalations.filter((e) => e.type === 'confirmation').length !== 1 ? 's' : ''}
                  {openEscalations.filter((e) => e.type !== 'confirmation').length > 0 &&
                    ` · ${openEscalations.filter((e) => e.type !== 'confirmation').length} other`
                  }
                </p>
              </div>
              <div className="text-right">
                <p className="text-xs text-muted-foreground">Longest waiting</p>
                <p className="text-sm font-medium text-amber-400 flex items-center gap-1 justify-end">
                  <Timer className="h-3.5 w-3.5" />
                  {Math.max(...openEscalations.map((e) => e.hours_waiting || 0))}h
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {escalations.length === 0 ? (
        <Card>
          <CardContent className="p-8 text-center">
            <CheckCircle2 className="h-10 w-10 text-green-400 mx-auto mb-3" />
            <p className="text-muted-foreground">No escalations - all clear!</p>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* Open Escalations */}
          {openEscalations.length > 0 && (
            <div>
              <h3 className="text-sm font-medium text-muted-foreground mb-3 uppercase tracking-wider">
                Open Escalations ({openEscalations.length})
              </h3>
              <div className="space-y-3">
                {openEscalations.map((esc) => (
                  <Card key={esc.id} className="border-red-500/30">
                    <CardContent className="p-4">
                      <div className="flex items-start gap-3">
                        {getEscalationIcon(esc.type)}
                        <div className="flex-1">
                          <div className="flex flex-wrap items-center gap-2 mb-1">
                            <Badge className="bg-red-500/20 text-red-400 border-0">
                              {getEscalationLabel(esc.type)}
                            </Badge>
                            {getEscalationStatusBadge(esc)}
                            {esc.hours_waiting && (
                              <span className="text-xs text-amber-400 flex items-center gap-1">
                                <Timer className="h-3 w-3" />
                                {esc.hours_waiting}h waiting
                              </span>
                            )}
                            <span className="text-xs text-muted-foreground">
                              Due {formatDate(esc.due_date)}
                            </span>
                          </div>

                          {/* Technician and role info */}
                          <div className="flex items-center gap-4 mb-1">
                            <div className="flex items-center gap-2">
                              <div className="h-7 w-7 rounded-full bg-primary/20 flex items-center justify-center text-[10px] font-medium text-primary shrink-0">
                                {esc.technician_name.split(' ').map((n) => n[0]).join('')}
                              </div>
                              <span className="text-sm font-medium">{esc.technician_name}</span>
                            </div>
                            {esc.role_name && (
                              <span className="text-xs text-muted-foreground">
                                Role: <span className="text-foreground">{esc.role_name}</span>
                              </span>
                            )}
                            {esc.partner_name && (
                              <span className="text-xs text-muted-foreground flex items-center gap-1">
                                <Building2 className="h-3 w-3" />
                                {esc.partner_name}
                              </span>
                            )}
                          </div>

                          <p className="text-sm text-muted-foreground">{esc.description}</p>

                          {/* Confirmation details */}
                          {esc.confirmation_type && esc.requested_date && (
                            <div className="flex items-center gap-3 mt-2 text-xs text-muted-foreground">
                              <span>
                                Confirmation: <span className="text-foreground capitalize">{esc.confirmation_type.replace('_', ' ')}</span>
                              </span>
                              <span>
                                Requested date: <span className="text-foreground">{formatDate(esc.requested_date)}</span>
                              </span>
                            </div>
                          )}

                          {/* Resolution panel */}
                          {resolvingId === esc.id ? (
                            <EscalationResolvePanel
                              escalation={esc}
                              project={project}
                              onResolve={(resolution, newTechName) => handleResolve(esc.id, resolution, newTechName)}
                              onCancel={() => setResolvingId(null)}
                            />
                          ) : null}
                        </div>

                        {/* Action buttons */}
                        {resolvingId !== esc.id && (
                          <div className="flex flex-col gap-1.5 shrink-0">
                            {esc.escalation_status !== 'ops_reviewing' && (
                              <Button
                                size="sm"
                                variant="ghost"
                                className="h-7 text-xs text-blue-400 hover:text-blue-300 hover:bg-blue-500/10"
                                onClick={() => acknowledgeEscalation(project.id, esc.id)}
                              >
                                <Eye className="h-3.5 w-3.5 mr-1" />
                                Review
                              </Button>
                            )}
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-7 text-xs"
                              onClick={() => setResolvingId(esc.id)}
                            >
                              <RotateCcw className="h-3.5 w-3.5 mr-1" />
                              Resolve
                            </Button>
                          </div>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          )}

          {/* Resolved Escalations */}
          {resolvedEscalations.length > 0 && (
            <div>
              <button
                onClick={() => setShowResolved(!showResolved)}
                className="flex items-center gap-2 text-sm font-medium text-muted-foreground mb-3 uppercase tracking-wider hover:text-foreground transition-colors"
              >
                <ChevronRight className={cn('h-4 w-4 transition-transform', showResolved && 'rotate-90')} />
                Resolved ({resolvedEscalations.length})
              </button>
              {showResolved && (
                <div className="space-y-2">
                  {resolvedEscalations.map((esc) => (
                    <Card key={esc.id} className="opacity-70 hover:opacity-100 transition-opacity">
                      <CardContent className="p-3">
                        <div className="flex items-start gap-3">
                          <CheckCircle2 className="h-4 w-4 text-green-400 mt-0.5 shrink-0" />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-0.5">
                              <span className="font-medium text-sm">{esc.technician_name}</span>
                              <span className="text-muted-foreground text-sm">- {getEscalationLabel(esc.type)}</span>
                              {getEscalationStatusBadge(esc)}
                            </div>
                            {esc.resolution_note && (
                              <div className="flex items-start gap-1.5 mt-1">
                                <MessageSquare className="h-3 w-3 text-muted-foreground mt-0.5 shrink-0" />
                                <p className="text-xs text-muted-foreground italic">{esc.resolution_note}</p>
                              </div>
                            )}
                            {esc.resolved_at && (
                              <p className="text-xs text-muted-foreground mt-1">
                                Resolved {formatDate(esc.resolved_at)}
                                {esc.resolved_by && ` by ${esc.resolved_by}`}
                              </p>
                            )}
                          </div>
                          <span className="text-xs text-muted-foreground shrink-0">
                            {formatDate(esc.created_at)}
                          </span>
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}

// --- Cross-Project Escalation Banner (shown on list view) ---
function EscalationBanner({ projects, onNavigate }: { projects: Project[]; onNavigate: (projectId: string) => void }) {
  const allEscalations = projects.flatMap((p) =>
    (p.escalations || [])
      .filter((e) => e.status === 'Open')
      .map((e) => ({ ...e, project_name: p.name, project_id: p.id }))
  )

  if (allEscalations.length === 0) return null

  const confirmationEscalations = allEscalations.filter((e) => e.type === 'confirmation')
  const otherEscalations = allEscalations.filter((e) => e.type !== 'confirmation')

  return (
    <Card className="border-red-500/30 bg-red-500/5 mb-4">
      <CardContent className="p-4">
        <div className="flex items-start gap-3">
          <div className="h-10 w-10 rounded-full bg-red-500/20 flex items-center justify-center shrink-0">
            <AlertTriangle className="h-5 w-5 text-red-400" />
          </div>
          <div className="flex-1">
            <h3 className="font-semibold text-red-400 mb-1">
              {allEscalations.length} Escalation{allEscalations.length > 1 ? 's' : ''} Across Projects
            </h3>
            <div className="space-y-2">
              {confirmationEscalations.length > 0 && (
                <div>
                  <p className="text-xs text-muted-foreground uppercase tracking-wider mb-1">
                    Unconfirmed Assignments ({confirmationEscalations.length})
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {confirmationEscalations.map((esc) => (
                      <button
                        key={esc.id}
                        onClick={() => onNavigate(esc.project_id)}
                        className="flex items-center gap-2 px-2.5 py-1.5 rounded-md bg-background border border-red-500/20 hover:border-red-500/50 transition-colors text-left"
                      >
                        <div className="h-6 w-6 rounded-full bg-primary/20 flex items-center justify-center text-[9px] font-medium text-primary shrink-0">
                          {esc.technician_name.split(' ').map((n: string) => n[0]).join('')}
                        </div>
                        <div className="min-w-0">
                          <p className="text-xs font-medium truncate">{esc.technician_name}</p>
                          <p className="text-[10px] text-muted-foreground truncate">{esc.project_name}</p>
                        </div>
                        <span className="text-[10px] text-amber-400 whitespace-nowrap">
                          {esc.hours_waiting}h
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {otherEscalations.length > 0 && (
                <div>
                  <p className="text-xs text-muted-foreground uppercase tracking-wider mb-1">
                    Other Escalations ({otherEscalations.length})
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {otherEscalations.map((esc) => (
                      <button
                        key={esc.id}
                        onClick={() => onNavigate(esc.project_id)}
                        className="flex items-center gap-2 px-2.5 py-1.5 rounded-md bg-background border border-amber-500/20 hover:border-amber-500/50 transition-colors text-xs"
                      >
                        <span className="font-medium">{esc.technician_name}</span>
                        <span className="text-muted-foreground">{esc.project_name}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

// --- Main Project Staffing Page ---
export default function ProjectStaffing() {
  const [searchParams, setSearchParams] = useSearchParams()
  const projectId = searchParams.get('project')
  const { projects, setProjects, getTotalOpenEscalations } = useProjectStore()

  useEffect(() => {
    if (projects.length === 0) {
      // Data will be loaded by ProjectListView's API fetch;
      // fallback to mock only if still empty after mount
      api.get('/projects')
        .then((res) => {
          const apiProjects = (res.data.items || []).map((p: any) => ({
            ...p,
            roles: (p.roles || []).map((r: any) => ({
              ...r,
              skill_bundle: (r.required_skills || []).map((s: any) => ({
                skill_name: s.skill_name || s.skill || '',
                min_proficiency: s.min_proficiency || s.min_level || 'Beginner',
              })),
            })),
            assignments: p.assignments || [],
            timesheets: p.timesheets || [],
            escalations: p.escalations || [],
          }))
          setProjects(apiProjects.length > 0 ? apiProjects : mockProjects)
        })
        .catch(() => setProjects(mockProjects))
    }
  }, [projects.length, setProjects])

  const selectedProject = projects.find((p) => p.id === projectId)
  const totalEscalations = getTotalOpenEscalations()

  const handleSelectProject = (id: string) => {
    setSearchParams({ project: id })
  }

  const handleBack = () => {
    setSearchParams({})
  }

  return (
    <div className="p-6 max-w-7xl mx-auto">
      {!selectedProject && (
        <>
          <div className="mb-6">
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold">Project Staffing</h1>
              {totalEscalations > 0 && (
                <Badge className="bg-red-500/20 text-red-400 border-red-500/30 text-xs">
                  <AlertTriangle className="h-3 w-3 mr-1" />
                  {totalEscalations} Escalation{totalEscalations > 1 ? 's' : ''}
                </Badge>
              )}
            </div>
            <p className="text-muted-foreground mt-1">
              Manage project assignments, roles, timesheets, and escalations
            </p>
          </div>
          <EscalationBanner projects={projects} onNavigate={handleSelectProject} />
        </>
      )}

      {selectedProject ? (
        <ProjectDetailView project={selectedProject} onBack={handleBack} />
      ) : (
        <ProjectListView onSelectProject={handleSelectProject} />
      )}
    </div>
  )
}
