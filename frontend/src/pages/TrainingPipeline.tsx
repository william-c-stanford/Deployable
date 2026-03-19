import { useEffect, useState, useCallback } from 'react';
import { useTrainingStore } from '@/stores/trainingStore';
import { KanbanColumn } from '@/components/training/KanbanColumn';
import { SkillMatrix } from '@/components/training/SkillMatrix';
import { SkillMatrixTable } from '@/components/training/SkillMatrixTable';
import { TrainingFilters } from '@/components/training/TrainingFilters';
import { TrainingStats } from '@/components/training/TrainingStats';
import { getSeedTechnicians } from '@/lib/seedData';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import type { CareerStage } from '@/types';
import {
  Search, UserCheck, GraduationCap, CheckCircle2,
  Clock, Rocket, LayoutGrid, Table2, RefreshCw,
} from 'lucide-react';

const KANBAN_COLUMNS: {
  stage: CareerStage;
  color: string;
  icon: React.ReactNode;
}[] = [
  {
    stage: 'Sourced',
    color: 'bg-gray-400',
    icon: <Search className="w-4 h-4 text-gray-400" />,
  },
  {
    stage: 'Screened',
    color: 'bg-blue-400',
    icon: <UserCheck className="w-4 h-4 text-blue-400" />,
  },
  {
    stage: 'In Training',
    color: 'bg-amber-400',
    icon: <GraduationCap className="w-4 h-4 text-amber-400" />,
  },
  {
    stage: 'Training Completed',
    color: 'bg-emerald-400',
    icon: <CheckCircle2 className="w-4 h-4 text-emerald-400" />,
  },
  {
    stage: 'Awaiting Assignment',
    color: 'bg-sky-400',
    icon: <Clock className="w-4 h-4 text-sky-400" />,
  },
  {
    stage: 'Deployed',
    color: 'bg-violet-400',
    icon: <Rocket className="w-4 h-4 text-violet-400" />,
  },
];

type ViewMode = 'kanban' | 'matrix';

export function TrainingPipeline() {
  const [viewMode, setViewMode] = useState<ViewMode>('kanban');
  const [isLoading, setIsLoading] = useState(false);
  const {
    technicians,
    setTechnicians,
    selectedTechnicianId,
    isSkillMatrixOpen,
    openSkillMatrix,
    closeSkillMatrix,
    moveTechnician,
    getTechniciansByStage,
  } = useTrainingStore();

  const loadTechnicians = useCallback(async () => {
    setIsLoading(true);
    try {
      // Try to fetch from backend API
      const response = await fetch('/api/technicians');
      if (response.ok) {
        const data = await response.json();
        if (Array.isArray(data) && data.length > 0) {
          setTechnicians(data);
          return;
        }
      }
    } catch {
      // Backend unavailable, fall through to seed data
    }
    // Fallback to seed data
    setTechnicians(getSeedTechnicians());
    setIsLoading(false);
  }, [setTechnicians]);

  // Load data on mount
  useEffect(() => {
    if (technicians.length === 0) {
      loadTechnicians();
    }
  }, [technicians.length, loadTechnicians]);

  const selectedTechnician = selectedTechnicianId
    ? technicians.find((t) => t.id === selectedTechnicianId)
    : null;

  const handleDrop = async (techId: string, newStage: CareerStage) => {
    moveTechnician(techId, newStage);
    // Attempt to persist to backend
    try {
      await fetch(`/api/technicians/${techId}/career-stage`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ career_stage: newStage }),
      });
    } catch {
      // Optimistic update already applied
    }
  };

  return (
    <div className="flex flex-col h-full max-w-full">
      {/* Page Header */}
      <div className="shrink-0 px-6 pt-6 pb-4 border-b space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
              <GraduationCap className="w-7 h-7 text-primary" />
              Training Pipeline
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              Track technician progression from sourcing through deployment. Drag cards between columns to update career stage.
            </p>
          </div>
          <div className="flex items-center gap-3">
            {/* View Toggle */}
            <div className="flex items-center rounded-lg border bg-card p-1 gap-0.5">
              <Button
                variant={viewMode === 'kanban' ? 'default' : 'ghost'}
                size="sm"
                onClick={() => setViewMode('kanban')}
                className="h-7 px-2.5 text-xs"
              >
                <LayoutGrid className="w-3.5 h-3.5 mr-1" />
                Board
              </Button>
              <Button
                variant={viewMode === 'matrix' ? 'default' : 'ghost'}
                size="sm"
                onClick={() => setViewMode('matrix')}
                className="h-7 px-2.5 text-xs"
              >
                <Table2 className="w-3.5 h-3.5 mr-1" />
                Matrix
              </Button>
            </div>

            {/* Refresh */}
            <Button
              variant="outline"
              size="sm"
              onClick={loadTechnicians}
              disabled={isLoading}
              className="h-8"
            >
              <RefreshCw className={`w-3.5 h-3.5 mr-1 ${isLoading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>

            {/* Count */}
            <div className="text-right hidden sm:block">
              <div className="text-2xl font-bold">{technicians.length}</div>
              <div className="text-xs text-muted-foreground">Total Technicians</div>
            </div>
          </div>
        </div>

        {/* Stats Row */}
        <TrainingStats />

        {/* Filters */}
        <TrainingFilters />
      </div>

      {/* Content Area */}
      {viewMode === 'kanban' ? (
        /* Kanban Board - horizontal scroll */
        <div className="flex-1 overflow-x-auto p-4">
          <div className="flex gap-4 min-w-max h-full pb-4">
            {KANBAN_COLUMNS.map(({ stage, color, icon }) => (
              <KanbanColumn
                key={stage}
                stage={stage}
                technicians={getTechniciansByStage(stage)}
                onDrillIn={openSkillMatrix}
                onDrop={handleDrop}
                color={color}
                icon={icon}
              />
            ))}
          </div>
        </div>
      ) : (
        /* Skill Matrix Table View */
        <div className="flex-1 overflow-auto p-4">
          <SkillMatrixTable
            onDrillIn={openSkillMatrix}
          />
        </div>
      )}

      {/* Skill Matrix Drill-in Panel */}
      {isSkillMatrixOpen && selectedTechnician && (
        <SkillMatrix
          technician={selectedTechnician}
          onClose={closeSkillMatrix}
        />
      )}
    </div>
  );
}
