import { create } from 'zustand';
import type { Technician, CareerStage } from '@/types';

interface TrainingFilters {
  search: string;
  skillFilter: string;
  regionFilter: string;
}

interface TrainingStore {
  technicians: Technician[];
  selectedTechnicianId: string | null;
  filters: TrainingFilters;
  isSkillMatrixOpen: boolean;
  setTechnicians: (techs: Technician[]) => void;
  selectTechnician: (id: string | null) => void;
  setFilters: (filters: Partial<TrainingFilters>) => void;
  openSkillMatrix: (id: string) => void;
  closeSkillMatrix: () => void;
  moveTechnician: (techId: string, newStage: CareerStage) => void;
  getTechniciansByStage: (stage: CareerStage) => Technician[];
}

export const useTrainingStore = create<TrainingStore>((set, get) => ({
  technicians: [],
  selectedTechnicianId: null,
  filters: {
    search: '',
    skillFilter: '',
    regionFilter: '',
  },
  isSkillMatrixOpen: false,

  setTechnicians: (technicians) => set({ technicians }),

  selectTechnician: (id) => set({ selectedTechnicianId: id }),

  setFilters: (newFilters) =>
    set((state) => ({
      filters: { ...state.filters, ...newFilters },
    })),

  openSkillMatrix: (id) =>
    set({ selectedTechnicianId: id, isSkillMatrixOpen: true }),

  closeSkillMatrix: () =>
    set({ isSkillMatrixOpen: false }),

  moveTechnician: (techId, newStage) =>
    set((state) => ({
      technicians: state.technicians.map((t) =>
        t.id === techId ? { ...t, career_stage: newStage } : t
      ),
    })),

  getTechniciansByStage: (stage) => {
    const { technicians, filters } = get();
    return technicians.filter((t) => {
      if (t.career_stage !== stage) return false;
      if (filters.search) {
        const q = filters.search.toLowerCase();
        if (
          !t.name.toLowerCase().includes(q) &&
          !t.home_base_city.toLowerCase().includes(q)
        )
          return false;
      }
      if (filters.skillFilter) {
        if (!t.skills.some((s) => s.skill_name === filters.skillFilter))
          return false;
      }
      if (filters.regionFilter) {
        if (!t.approved_regions.includes(filters.regionFilter)) return false;
      }
      return true;
    });
  },
}));
