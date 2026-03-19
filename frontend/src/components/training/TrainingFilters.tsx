import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Button } from '@/components/ui/button';
import { useTrainingStore } from '@/stores/trainingStore';
import { getAllSkills, getAllRegions } from '@/lib/seedData';
import { Search, X } from 'lucide-react';

export function TrainingFilters() {
  const { filters, setFilters } = useTrainingStore();
  const allSkills = getAllSkills();
  const allRegions = getAllRegions();

  const hasActiveFilters = filters.search || filters.skillFilter || filters.regionFilter;

  return (
    <div className="flex flex-wrap items-center gap-3">
      {/* Search */}
      <div className="relative flex-1 min-w-[200px] max-w-sm">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
        <Input
          placeholder="Search technicians..."
          value={filters.search}
          onChange={(e) => setFilters({ search: e.target.value })}
          className="pl-9"
        />
      </div>

      {/* Skill Filter */}
      <Select
        value={filters.skillFilter || "all"}
        onValueChange={(val) => setFilters({ skillFilter: val === "all" ? "" : val })}
      >
        <SelectTrigger className="w-[180px]">
          <SelectValue placeholder="All Skills" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All Skills</SelectItem>
          {allSkills.map((skill) => (
            <SelectItem key={skill} value={skill}>{skill}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Region Filter */}
      <Select
        value={filters.regionFilter || "all"}
        onValueChange={(val) => setFilters({ regionFilter: val === "all" ? "" : val })}
      >
        <SelectTrigger className="w-[140px]">
          <SelectValue placeholder="All Regions" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All Regions</SelectItem>
          {allRegions.map((region) => (
            <SelectItem key={region} value={region}>{region}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Clear Filters */}
      {hasActiveFilters && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setFilters({ search: '', skillFilter: '', regionFilter: '' })}
          className="text-muted-foreground"
        >
          <X className="w-4 h-4 mr-1" />
          Clear
        </Button>
      )}
    </div>
  );
}
