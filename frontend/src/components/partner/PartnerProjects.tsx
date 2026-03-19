import { usePartnerStore } from "@/stores/partnerStore";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
  MapPin,
  Calendar,
  Users,
  FolderKanban,
} from "lucide-react";

const statusColors: Record<string, string> = {
  Draft: "bg-muted text-muted-foreground",
  Staffing: "bg-blue-500",
  Active: "bg-emerald-600",
  "Wrapping Up": "bg-amber-500",
  "On Hold": "bg-orange-500",
  Closed: "bg-muted text-muted-foreground",
};

export function PartnerProjects() {
  const { projects, isLoading } = usePartnerStore();

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {[1, 2, 3].map((i) => (
          <Card key={i}>
            <CardContent className="p-5">
              <div className="space-y-3">
                <div className="h-5 w-48 rounded bg-muted animate-pulse" />
                <div className="h-4 w-32 rounded bg-muted animate-pulse" />
                <div className="h-3 w-full rounded bg-muted animate-pulse" />
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  if (projects.length === 0) {
    return (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center justify-center py-12">
          <FolderKanban className="h-10 w-10 text-muted-foreground mb-3" />
          <h3 className="font-semibold text-lg">No Projects</h3>
          <p className="text-sm text-muted-foreground mt-1">
            Projects will appear here once created.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {projects.map((project) => {
        const fillPercent =
          project.total_roles > 0
            ? Math.round((project.filled_roles / project.total_roles) * 100)
            : 0;

        return (
          <Card key={project.id} className="border hover:border-primary/20 transition-colors">
            <CardContent className="p-4 md:p-5">
              <div className="flex flex-col gap-3">
                {/* Header */}
                <div className="flex items-start justify-between gap-2">
                  <h3 className="font-semibold text-sm leading-tight">{project.name}</h3>
                  <Badge className={`text-[10px] shrink-0 ${statusColors[project.status] || ""}`}>
                    {project.status}
                  </Badge>
                </div>

                {/* Location & dates */}
                <div className="flex flex-col gap-1.5 text-xs text-muted-foreground">
                  <div className="flex items-center gap-1.5">
                    <MapPin className="h-3 w-3" />
                    <span>
                      {project.location_city ? `${project.location_city}, ` : ""}
                      {project.location_region}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Calendar className="h-3 w-3" />
                    <span>
                      {new Date(project.start_date).toLocaleDateString("en-US", {
                        month: "short",
                        day: "numeric",
                        year: "numeric",
                      })}
                      {project.end_date &&
                        ` - ${new Date(project.end_date).toLocaleDateString("en-US", {
                          month: "short",
                          day: "numeric",
                          year: "numeric",
                        })}`}
                    </span>
                  </div>
                </div>

                {/* Staffing progress */}
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="flex items-center gap-1 text-muted-foreground">
                      <Users className="h-3 w-3" />
                      Staffing
                    </span>
                    <span className="font-medium">
                      {project.filled_roles}/{project.total_roles} filled
                    </span>
                  </div>
                  <Progress value={fillPercent} className="h-1.5" />
                </div>

                {/* Active assignments */}
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Badge variant="outline" className="text-[10px]">
                    {project.active_assignments} active assignment{project.active_assignments !== 1 ? "s" : ""}
                  </Badge>
                </div>
              </div>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
