import { Card } from '@/components/ui/card';
import { useTrainingStore } from '@/stores/trainingStore';
import { Users, GraduationCap, Clock, TrendingUp, AlertTriangle, CheckCircle2 } from 'lucide-react';
import type { CareerStage } from '@/types';

const STAGES: CareerStage[] = ['Sourced', 'Screened', 'In Training', 'Training Completed', 'Awaiting Assignment', 'Deployed'];

export function TrainingStats() {
  const { technicians } = useTrainingStore();

  const totalTechs = technicians.length;
  const inTraining = technicians.filter(t => t.career_stage === 'In Training').length;
  const readyNow = technicians.filter(t => t.deployability_status === 'Ready Now').length;
  const missingCerts = technicians.filter(t => t.deployability_status === 'Missing Cert').length;
  const totalHours = technicians.reduce(
    (sum, t) => sum + t.skills.reduce((s, sk) => s + sk.training_hours_accumulated, 0),
    0
  );
  const avgSkillsPerTech = totalTechs > 0
    ? (technicians.reduce((sum, t) => sum + t.skills.length, 0) / totalTechs).toFixed(1)
    : '0';

  const stats = [
    { label: 'Total Technicians', value: totalTechs, icon: Users, color: 'text-primary' },
    { label: 'In Training', value: inTraining, icon: GraduationCap, color: 'text-warning' },
    { label: 'Ready Now', value: readyNow, icon: CheckCircle2, color: 'text-success' },
    { label: 'Missing Certs', value: missingCerts, icon: AlertTriangle, color: 'text-destructive' },
    { label: 'Total Hours', value: totalHours.toLocaleString(), icon: Clock, color: 'text-info' },
    { label: 'Avg Skills/Tech', value: avgSkillsPerTech, icon: TrendingUp, color: 'text-primary' },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      {stats.map((stat) => (
        <Card key={stat.label} className="p-3">
          <div className="flex items-center gap-2 mb-1">
            <stat.icon className={`w-4 h-4 ${stat.color}`} />
            <span className="text-xs text-muted-foreground">{stat.label}</span>
          </div>
          <div className="text-2xl font-bold">{stat.value}</div>
        </Card>
      ))}
    </div>
  );
}
