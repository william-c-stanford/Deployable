import { useState } from 'react';
import { cn } from '@/lib/utils';
import { KanbanCard } from './KanbanCard';
import type { Technician, CareerStage } from '@/types';

interface KanbanColumnProps {
  stage: CareerStage;
  technicians: Technician[];
  onDrillIn: (id: string) => void;
  onDrop: (techId: string, stage: CareerStage) => void;
  color: string;
  icon: React.ReactNode;
}

export function KanbanColumn({ stage, technicians, onDrillIn, onDrop, color, icon }: KanbanColumnProps) {
  const [isOver, setIsOver] = useState(false);

  // Aggregate stats for the column
  const totalHours = technicians.reduce(
    (sum, t) => sum + t.skills.reduce((s, sk) => s + sk.training_hours_accumulated, 0), 0
  );

  return (
    <div
      className={cn(
        'flex flex-col min-w-[280px] max-w-[320px] flex-1 rounded-lg border transition-all duration-200',
        isOver ? 'border-primary bg-primary/5 shadow-lg shadow-primary/10' : 'border-border bg-card/30'
      )}
      onDragOver={(e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        setIsOver(true);
      }}
      onDragLeave={() => setIsOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setIsOver(false);
        const techId = e.dataTransfer.getData('text/plain');
        if (techId) {
          onDrop(techId, stage);
        }
      }}
    >
      {/* Column Header */}
      <div className="sticky top-0 z-10 p-3 border-b bg-card/80 backdrop-blur-sm rounded-t-lg">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className={cn('w-2.5 h-2.5 rounded-full', color)} />
            {icon}
            <h3 className="text-sm font-semibold">{stage}</h3>
          </div>
          <span className="flex items-center justify-center min-w-6 h-6 px-1.5 rounded-full bg-secondary text-xs font-bold">
            {technicians.length}
          </span>
        </div>
        {technicians.length > 0 && (
          <div className="flex items-center gap-3 mt-1.5 text-[10px] text-muted-foreground">
            <span>{totalHours.toLocaleString()}h total</span>
            <span>{technicians.filter(t => t.deployability_status === 'Ready Now').length} ready</span>
          </div>
        )}
      </div>

      {/* Cards */}
      <div className="flex-1 p-2 space-y-2 overflow-y-auto max-h-[calc(100vh-300px)]">
        {technicians.length === 0 ? (
          <div className={cn(
            'flex flex-col items-center justify-center h-24 text-sm text-muted-foreground border border-dashed rounded-md transition-colors',
            isOver && 'border-primary text-primary bg-primary/5'
          )}>
            <span>{isOver ? 'Drop here' : 'No technicians'}</span>
            {isOver && <span className="text-xs mt-0.5">Release to move</span>}
          </div>
        ) : (
          technicians.map((tech) => (
            <KanbanCard
              key={tech.id}
              technician={tech}
              onDrillIn={onDrillIn}
            />
          ))
        )}
      </div>
    </div>
  );
}
