import { useEffect, useState, useCallback } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Search,
  Filter,
  LayoutGrid,
  LayoutList,
  ChevronLeft,
  ChevronRight,
  MapPin,
  Calendar,
  Award,
  Shield,
  X,
  UserPlus,
  Users,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Separator } from "@/components/ui/separator";
import { useTechnicianStore, REGIONS, SKILLS_TAXONOMY } from "@/stores/technicianStore";
import { cn, formatDate } from "@/lib/utils";
import type { Technician, DeployabilityStatus, CareerStage } from "@/types/index";

// ---- Status badge color mapping ----

function getDeployabilityVariant(
  status: DeployabilityStatus
): "default" | "secondary" | "destructive" | "outline" | "success" | "warning" | "info" {
  switch (status) {
    case "Ready Now":
      return "success";
    case "In Training":
      return "info";
    case "Currently Assigned":
      return "default";
    case "Rolling Off Soon":
      return "warning";
    case "Missing Cert":
    case "Missing Docs":
      return "destructive";
    case "Inactive":
      return "secondary";
    default:
      return "outline";
  }
}

function getCareerStageBadge(stage: CareerStage) {
  const colors: Record<string, string> = {
    Sourced: "bg-slate-500/20 text-slate-300 border-slate-500/30",
    Screened: "bg-purple-500/20 text-purple-300 border-purple-500/30",
    "In Training": "bg-blue-500/20 text-blue-300 border-blue-500/30",
    "Training Completed": "bg-cyan-500/20 text-cyan-300 border-cyan-500/30",
    "Awaiting Assignment": "bg-amber-500/20 text-amber-300 border-amber-500/30",
    Deployed: "bg-emerald-500/20 text-emerald-300 border-emerald-500/30",
  };
  return colors[stage] || "";
}

function getInitials(name: string): string {
  return name
    .split(" ")
    .map((n) => n[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);
}

// ---- Filter Bar ----

function FilterBar() {
  const { filters, setFilters, resetFilters } = useTechnicianStore();
  const [searchInput, setSearchInput] = useState(filters.search);
  const [searchParams, setSearchParams] = useSearchParams();

  // Sync URL params to filters on mount
  useEffect(() => {
    const urlFilters: Record<string, string> = {};
    searchParams.forEach((value, key) => {
      if (key in filters) {
        urlFilters[key] = value;
      }
    });
    if (Object.keys(urlFilters).length > 0) {
      setFilters(urlFilters);
      if (urlFilters.search) setSearchInput(urlFilters.search);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Update URL when filters change
  useEffect(() => {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    setSearchParams(params, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters]);

  const handleSearchSubmit = useCallback(() => {
    setFilters({ search: searchInput });
  }, [searchInput, setFilters]);

  const hasActiveFilters = Object.values(filters).some((v) => v !== "");

  return (
    <div className="space-y-3">
      {/* Search bar */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search by name or email..."
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearchSubmit()}
            className="pl-10"
          />
        </div>
        <Button onClick={handleSearchSubmit} size="default">
          <Search className="mr-2 h-4 w-4" />
          Search
        </Button>
      </div>

      {/* Filter row */}
      <div className="flex flex-wrap items-center gap-2">
        <Filter className="h-4 w-4 text-muted-foreground" />

        <Select
          value={filters.career_stage || "all"}
          onValueChange={(v) =>
            setFilters({ career_stage: v === "all" ? "" : v })
          }
        >
          <SelectTrigger className="w-[180px] h-9">
            <SelectValue placeholder="Career Stage" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Stages</SelectItem>
            <SelectItem value="Sourced">Sourced</SelectItem>
            <SelectItem value="Screened">Screened</SelectItem>
            <SelectItem value="In Training">In Training</SelectItem>
            <SelectItem value="Training Completed">Training Completed</SelectItem>
            <SelectItem value="Awaiting Assignment">Awaiting Assignment</SelectItem>
            <SelectItem value="Deployed">Deployed</SelectItem>
          </SelectContent>
        </Select>

        <Select
          value={filters.deployability_status || "all"}
          onValueChange={(v) =>
            setFilters({ deployability_status: v === "all" ? "" : v })
          }
        >
          <SelectTrigger className="w-[180px] h-9">
            <SelectValue placeholder="Deployability" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Status</SelectItem>
            <SelectItem value="Ready Now">Ready Now</SelectItem>
            <SelectItem value="In Training">In Training</SelectItem>
            <SelectItem value="Currently Assigned">Currently Assigned</SelectItem>
            <SelectItem value="Missing Cert">Missing Cert</SelectItem>
            <SelectItem value="Missing Docs">Missing Docs</SelectItem>
            <SelectItem value="Rolling Off Soon">Rolling Off Soon</SelectItem>
            <SelectItem value="Inactive">Inactive</SelectItem>
          </SelectContent>
        </Select>

        <Select
          value={filters.region || "all"}
          onValueChange={(v) =>
            setFilters({ region: v === "all" ? "" : v })
          }
        >
          <SelectTrigger className="w-[160px] h-9">
            <SelectValue placeholder="Region" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Regions</SelectItem>
            {REGIONS.map((r) => (
              <SelectItem key={r} value={r}>
                {r}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select
          value={filters.skill || "all"}
          onValueChange={(v) =>
            setFilters({ skill: v === "all" ? "" : v })
          }
        >
          <SelectTrigger className="w-[180px] h-9">
            <SelectValue placeholder="Skill" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Skills</SelectItem>
            {SKILLS_TAXONOMY.map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Input
          type="date"
          placeholder="Available before"
          value={filters.available_before}
          onChange={(e) => setFilters({ available_before: e.target.value })}
          className="w-[160px] h-9"
        />

        {hasActiveFilters && (
          <Button
            variant="ghost"
            size="sm"
            onClick={resetFilters}
            className="h-9 text-muted-foreground hover:text-foreground"
          >
            <X className="mr-1 h-3 w-3" />
            Clear
          </Button>
        )}
      </div>

      {/* Active filter chips */}
      {hasActiveFilters && (
        <div className="flex flex-wrap gap-1.5">
          {filters.search && (
            <Badge variant="secondary" className="gap-1">
              Search: {filters.search}
              <X
                className="h-3 w-3 cursor-pointer"
                onClick={() => {
                  setFilters({ search: "" });
                  setSearchInput("");
                }}
              />
            </Badge>
          )}
          {filters.career_stage && (
            <Badge variant="secondary" className="gap-1">
              Stage: {filters.career_stage}
              <X
                className="h-3 w-3 cursor-pointer"
                onClick={() => setFilters({ career_stage: "" })}
              />
            </Badge>
          )}
          {filters.deployability_status && (
            <Badge variant="secondary" className="gap-1">
              Status: {filters.deployability_status}
              <X
                className="h-3 w-3 cursor-pointer"
                onClick={() => setFilters({ deployability_status: "" })}
              />
            </Badge>
          )}
          {filters.region && (
            <Badge variant="secondary" className="gap-1">
              Region: {filters.region}
              <X
                className="h-3 w-3 cursor-pointer"
                onClick={() => setFilters({ region: "" })}
              />
            </Badge>
          )}
          {filters.skill && (
            <Badge variant="secondary" className="gap-1">
              Skill: {filters.skill}
              <X
                className="h-3 w-3 cursor-pointer"
                onClick={() => setFilters({ skill: "" })}
              />
            </Badge>
          )}
          {filters.available_before && (
            <Badge variant="secondary" className="gap-1">
              Available by: {filters.available_before}
              <X
                className="h-3 w-3 cursor-pointer"
                onClick={() => setFilters({ available_before: "" })}
              />
            </Badge>
          )}
        </div>
      )}
    </div>
  );
}

// ---- Table View ----

function TechnicianTableView({ technicians }: { technicians: Technician[] }) {
  const navigate = useNavigate();

  return (
    <div className="rounded-lg border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-[280px]">Technician</TableHead>
            <TableHead>Career Stage</TableHead>
            <TableHead>Deployability</TableHead>
            <TableHead>Location</TableHead>
            <TableHead>Skills</TableHead>
            <TableHead>Certs</TableHead>
            <TableHead>Available</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {technicians.map((tech) => (
            <TableRow
              key={tech.id}
              className="cursor-pointer"
              onClick={() => navigate(`/ops/technicians/${tech.id}`)}
            >
              <TableCell>
                <div className="flex items-center gap-3">
                  <Avatar className="h-9 w-9">
                    <AvatarFallback className="text-xs bg-primary/10 text-primary">
                      {getInitials(tech.name)}
                    </AvatarFallback>
                  </Avatar>
                  <div>
                    <p className="font-medium text-sm">{tech.name}</p>
                    <p className="text-xs text-muted-foreground">{tech.email}</p>
                  </div>
                </div>
              </TableCell>
              <TableCell>
                <span
                  className={cn(
                    "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium",
                    getCareerStageBadge(tech.career_stage as CareerStage)
                  )}
                >
                  {tech.career_stage}
                </span>
              </TableCell>
              <TableCell>
                <Badge variant={getDeployabilityVariant(tech.deployability_status as DeployabilityStatus)}>
                  {tech.deployability_status}
                </Badge>
              </TableCell>
              <TableCell>
                <div className="flex items-center gap-1 text-sm text-muted-foreground">
                  <MapPin className="h-3 w-3" />
                  {tech.home_base_city}
                </div>
              </TableCell>
              <TableCell>
                <div className="flex flex-wrap gap-1 max-w-[200px]">
                  {tech.skills.slice(0, 3).map((s) => (
                    <span
                      key={s.skill_name}
                      className="inline-flex items-center rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
                    >
                      {s.skill_name}
                    </span>
                  ))}
                  {tech.skills.length > 3 && (
                    <span className="text-[10px] text-muted-foreground">
                      +{tech.skills.length - 3}
                    </span>
                  )}
                </div>
              </TableCell>
              <TableCell>
                <div className="flex items-center gap-1">
                  <Shield className="h-3 w-3 text-muted-foreground" />
                  <span className="text-sm">{tech.certifications.length}</span>
                  {tech.certifications.some((c) => c.status === "Expired") && (
                    <span className="h-2 w-2 rounded-full bg-destructive" />
                  )}
                </div>
              </TableCell>
              <TableCell>
                <div className="flex items-center gap-1 text-sm text-muted-foreground">
                  <Calendar className="h-3 w-3" />
                  {formatDate(tech.available_from)}
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

// ---- Card View ----

function TechnicianCardView({ technicians }: { technicians: Technician[] }) {
  const navigate = useNavigate();

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
      {technicians.map((tech) => (
        <Card
          key={tech.id}
          className="cursor-pointer hover:border-primary/50 transition-colors"
          onClick={() => navigate(`/ops/technicians/${tech.id}`)}
        >
          <CardHeader className="pb-3">
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-3">
                <Avatar className="h-11 w-11">
                  <AvatarFallback className="bg-primary/10 text-primary text-sm">
                    {getInitials(tech.name)}
                  </AvatarFallback>
                </Avatar>
                <div>
                  <p className="font-semibold text-sm">{tech.name}</p>
                  <div className="flex items-center gap-1 text-xs text-muted-foreground mt-0.5">
                    <MapPin className="h-3 w-3" />
                    {tech.home_base_city}
                  </div>
                </div>
              </div>
              <Badge
                variant={getDeployabilityVariant(tech.deployability_status as DeployabilityStatus)}
                className="text-[10px]"
              >
                {tech.deployability_status}
              </Badge>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center justify-between">
              <span
                className={cn(
                  "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium",
                  getCareerStageBadge(tech.career_stage as CareerStage)
                )}
              >
                {tech.career_stage}
              </span>
              <span className="text-xs text-muted-foreground">
                Available {formatDate(tech.available_from)}
              </span>
            </div>

            <Separator />

            {/* Skills */}
            <div>
              <p className="text-[10px] uppercase text-muted-foreground font-medium tracking-wider mb-1.5">
                Skills
              </p>
              <div className="flex flex-wrap gap-1">
                {tech.skills.slice(0, 4).map((s) => (
                  <span
                    key={s.skill_name}
                    className={cn(
                      "inline-flex items-center rounded px-1.5 py-0.5 text-[10px]",
                      s.proficiency_level === "Advanced"
                        ? "bg-emerald-500/20 text-emerald-300"
                        : s.proficiency_level === "Intermediate"
                        ? "bg-blue-500/20 text-blue-300"
                        : "bg-muted text-muted-foreground"
                    )}
                  >
                    {s.skill_name}
                  </span>
                ))}
                {tech.skills.length > 4 && (
                  <span className="text-[10px] text-muted-foreground">
                    +{tech.skills.length - 4} more
                  </span>
                )}
              </div>
            </div>

            {/* Badges & Certs count */}
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-1">
                  <Shield className="h-3 w-3" />
                  {tech.certifications.length} certs
                </div>
                {(tech.site_badges.length > 0 || tech.milestone_badges.length > 0) && (
                  <div className="flex items-center gap-1">
                    <Award className="h-3 w-3" />
                    {tech.site_badges.length + tech.milestone_badges.length} badges
                  </div>
                )}
              </div>
              {tech.certifications.some((c) => c.status === "Expired") && (
                <Badge variant="destructive" className="text-[10px] h-5">
                  Cert Issue
                </Badge>
              )}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// ---- Pagination ----

function Pagination() {
  const { page, pageSize, totalCount, setPage } = useTechnicianStore();
  const totalPages = Math.ceil(totalCount / pageSize);

  if (totalPages <= 1) return null;

  return (
    <div className="flex items-center justify-between">
      <p className="text-sm text-muted-foreground">
        Showing {(page - 1) * pageSize + 1}-{Math.min(page * pageSize, totalCount)} of{" "}
        {totalCount} technicians
      </p>
      <div className="flex items-center gap-1">
        <Button
          variant="outline"
          size="sm"
          disabled={page <= 1}
          onClick={() => setPage(page - 1)}
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>
        {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
          let pageNum: number;
          if (totalPages <= 7) {
            pageNum = i + 1;
          } else if (page <= 4) {
            pageNum = i + 1;
          } else if (page >= totalPages - 3) {
            pageNum = totalPages - 6 + i;
          } else {
            pageNum = page - 3 + i;
          }
          return (
            <Button
              key={pageNum}
              variant={pageNum === page ? "default" : "outline"}
              size="sm"
              className="w-9"
              onClick={() => setPage(pageNum)}
            >
              {pageNum}
            </Button>
          );
        })}
        <Button
          variant="outline"
          size="sm"
          disabled={page >= totalPages}
          onClick={() => setPage(page + 1)}
        >
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

// ---- Summary Stats ----

function DirectoryStats() {
  const { totalCount, technicians } = useTechnicianStore();

  const readyCount = technicians.filter(
    (t) => t.deployability_status === "Ready Now"
  ).length;
  const trainingCount = technicians.filter(
    (t) => t.deployability_status === "In Training"
  ).length;
  const assignedCount = technicians.filter(
    (t) => t.deployability_status === "Currently Assigned"
  ).length;

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      <div className="rounded-lg border bg-card p-3">
        <div className="flex items-center gap-2">
          <Users className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm text-muted-foreground">Total</span>
        </div>
        <p className="text-2xl font-bold mt-1">{totalCount}</p>
      </div>
      <div className="rounded-lg border bg-card p-3">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-emerald-500" />
          <span className="text-sm text-muted-foreground">Ready Now</span>
        </div>
        <p className="text-2xl font-bold mt-1 text-emerald-500">{readyCount}</p>
      </div>
      <div className="rounded-lg border bg-card p-3">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-blue-500" />
          <span className="text-sm text-muted-foreground">In Training</span>
        </div>
        <p className="text-2xl font-bold mt-1 text-blue-500">{trainingCount}</p>
      </div>
      <div className="rounded-lg border bg-card p-3">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-primary" />
          <span className="text-sm text-muted-foreground">Assigned</span>
        </div>
        <p className="text-2xl font-bold mt-1 text-primary">{assignedCount}</p>
      </div>
    </div>
  );
}

// ---- Main Page ----

export function TechnicianDirectory() {
  const { technicians, isLoading, viewMode, setViewMode, initialize } =
    useTechnicianStore();

  useEffect(() => {
    initialize();
  }, [initialize]);

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            Technician Directory
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Search, filter, and manage your technician workforce
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center rounded-lg border bg-card p-0.5">
            <Button
              variant={viewMode === "table" ? "default" : "ghost"}
              size="sm"
              className="h-8"
              onClick={() => setViewMode("table")}
            >
              <LayoutList className="h-4 w-4" />
            </Button>
            <Button
              variant={viewMode === "cards" ? "default" : "ghost"}
              size="sm"
              className="h-8"
              onClick={() => setViewMode("cards")}
            >
              <LayoutGrid className="h-4 w-4" />
            </Button>
          </div>
          <Button>
            <UserPlus className="mr-2 h-4 w-4" />
            Add Technician
          </Button>
        </div>
      </div>

      {/* Stats */}
      <DirectoryStats />

      {/* Filters */}
      <FilterBar />

      {/* Content */}
      {isLoading ? (
        <div className="flex items-center justify-center py-20">
          <div className="flex flex-col items-center gap-3">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            <p className="text-sm text-muted-foreground">Loading technicians...</p>
          </div>
        </div>
      ) : technicians.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <Users className="h-12 w-12 text-muted-foreground/50 mb-4" />
          <h3 className="text-lg font-semibold">No technicians found</h3>
          <p className="text-sm text-muted-foreground mt-1">
            Try adjusting your search or filter criteria
          </p>
        </div>
      ) : viewMode === "table" ? (
        <TechnicianTableView technicians={technicians} />
      ) : (
        <TechnicianCardView technicians={technicians} />
      )}

      {/* Pagination */}
      <Pagination />
    </div>
  );
}
