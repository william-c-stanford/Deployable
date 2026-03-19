import { create } from "zustand";
import api from "@/lib/api";
import type {
  Technician,
  Skill,
  Certification,
  TechDocument,
  CareerStage,
  DeployabilityStatus,
} from "@/types/index";

// ---- Mock data for demo (used when API is unavailable) ----

const SKILLS_TAXONOMY: string[] = [
  "Fiber Splicing",
  "OTDR Testing",
  "Cable Pulling",
  "Aerial Construction",
  "Underground Construction",
  "Network Design",
  "OSP Engineering",
  "Structured Cabling",
  "Data Center Ops",
  "Power Systems",
  "Safety Compliance",
  "Project Management",
  "Equipment Operation",
  "Blueprint Reading",
  "Conduit Installation",
];

const CERT_NAMES: string[] = [
  "OSHA 10",
  "OSHA 30",
  "CDL Class A",
  "CDL Class B",
  "FOA CFOT",
  "FOA CFOS/S",
  "CompTIA Network+",
  "BICSI Installer",
  "First Aid/CPR",
  "Forklift Certified",
  "Confined Space Entry",
  "Aerial Lift Certified",
];

const DOC_TYPES: string[] = [
  "Background Check",
  "Drug Screening",
  "Driver's License",
  "Social Security Card",
  "W-4 Form",
  "I-9 Employment",
  "Direct Deposit Form",
  "Safety Acknowledgment",
];

const CITIES: string[] = [
  "Atlanta, GA",
  "Dallas, TX",
  "Phoenix, AZ",
  "Denver, CO",
  "Nashville, TN",
  "Charlotte, NC",
  "Orlando, FL",
  "Portland, OR",
  "Kansas City, MO",
  "Salt Lake City, UT",
  "Indianapolis, IN",
  "Columbus, OH",
  "Richmond, VA",
  "Boise, ID",
  "Austin, TX",
  "Tampa, FL",
  "Raleigh, NC",
  "Minneapolis, MN",
  "Detroit, MI",
  "Sacramento, CA",
];

const REGIONS: string[] = [
  "Georgia",
  "Texas",
  "Arizona",
  "Colorado",
  "Tennessee",
  "North Carolina",
  "Florida",
  "Oregon",
  "Missouri",
  "Utah",
  "Indiana",
  "Ohio",
  "Virginia",
  "Idaho",
  "California",
  "Minnesota",
  "Michigan",
];

const FIRST_NAMES: string[] = [
  "Marcus",
  "Sarah",
  "James",
  "Maria",
  "David",
  "Emily",
  "Robert",
  "Jessica",
  "Michael",
  "Ashley",
  "Daniel",
  "Nicole",
  "Kevin",
  "Amanda",
  "Chris",
  "Stephanie",
  "Brian",
  "Megan",
  "Jason",
  "Rachel",
  "Tyler",
  "Hannah",
  "Brandon",
  "Samantha",
  "Justin",
  "Kayla",
  "Andrew",
  "Brittany",
  "Anthony",
  "Lauren",
  "Ryan",
  "Angela",
  "Juan",
  "Patricia",
  "Carlos",
  "Michelle",
  "Dwayne",
  "Tiffany",
  "Derek",
  "Crystal",
  "Travis",
  "Vanessa",
  "Cody",
  "Diana",
  "Malik",
  "Priya",
  "Hector",
  "Li Wei",
  "Dmitri",
  "Fatima",
  "Omar",
  "Kenji",
];

const LAST_NAMES: string[] = [
  "Johnson",
  "Williams",
  "Martinez",
  "Garcia",
  "Brown",
  "Davis",
  "Miller",
  "Wilson",
  "Anderson",
  "Taylor",
  "Thomas",
  "Jackson",
  "White",
  "Harris",
  "Clark",
  "Lewis",
  "Robinson",
  "Walker",
  "Young",
  "Allen",
  "King",
  "Wright",
  "Scott",
  "Torres",
  "Nguyen",
  "Hill",
  "Flores",
  "Green",
  "Adams",
  "Nelson",
  "Baker",
  "Hall",
  "Rivera",
  "Campbell",
  "Mitchell",
  "Carter",
  "Roberts",
  "Gomez",
  "Phillips",
  "Evans",
  "Turner",
  "Diaz",
  "Parker",
  "Cruz",
  "Edwards",
  "Collins",
  "Reyes",
  "Stewart",
  "Morris",
  "Morales",
  "Chen",
  "Patel",
];

const CAREER_STAGES: CareerStage[] = [
  "Sourced",
  "Screened",
  "In Training",
  "Training Completed",
  "Awaiting Assignment",
  "Deployed",
];

const DEPLOYABILITY_STATUSES: DeployabilityStatus[] = [
  "Ready Now",
  "In Training",
  "Currently Assigned",
  "Missing Cert",
  "Missing Docs",
  "Rolling Off Soon",
  "Inactive",
];

const PROFICIENCY_LEVELS = ["Beginner", "Intermediate", "Advanced"] as const;

function randomItem<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

function randomSubset<T>(arr: T[], min: number, max: number): T[] {
  const count = min + Math.floor(Math.random() * (max - min + 1));
  const shuffled = [...arr].sort(() => Math.random() - 0.5);
  return shuffled.slice(0, count);
}

function generateId(): string {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}

function generateMockTechnicians(count: number): Technician[] {
  const techs: Technician[] = [];

  // Archetype distribution
  const archetypes = [
    { stage: "Deployed" as CareerStage, status: "Currently Assigned" as DeployabilityStatus, count: 15 },
    { stage: "Deployed" as CareerStage, status: "Ready Now" as DeployabilityStatus, count: 8 },
    { stage: "Deployed" as CareerStage, status: "Rolling Off Soon" as DeployabilityStatus, count: 5 },
    { stage: "Awaiting Assignment" as CareerStage, status: "Ready Now" as DeployabilityStatus, count: 6 },
    { stage: "In Training" as CareerStage, status: "In Training" as DeployabilityStatus, count: 6 },
    { stage: "Training Completed" as CareerStage, status: "Missing Cert" as DeployabilityStatus, count: 3 },
    { stage: "Training Completed" as CareerStage, status: "Missing Docs" as DeployabilityStatus, count: 2 },
    { stage: "Screened" as CareerStage, status: "In Training" as DeployabilityStatus, count: 3 },
    { stage: "Sourced" as CareerStage, status: "Inactive" as DeployabilityStatus, count: 4 },
  ];

  let idx = 0;
  for (const arch of archetypes) {
    for (let i = 0; i < arch.count && idx < count; i++, idx++) {
      const firstName = FIRST_NAMES[idx % FIRST_NAMES.length];
      const lastName = LAST_NAMES[idx % LAST_NAMES.length];
      const city = CITIES[idx % CITIES.length];
      const regions = randomSubset(REGIONS, 1, 4);

      // Skills based on career stage
      const skillCount =
        arch.stage === "Deployed" ? { min: 3, max: 7 } :
        arch.stage === "In Training" ? { min: 1, max: 3 } :
        { min: 2, max: 5 };

      const skills: Skill[] = randomSubset(SKILLS_TAXONOMY, skillCount.min, skillCount.max).map(
        (name) => {
          const profLevel =
            arch.stage === "Deployed"
              ? randomItem(["Intermediate", "Advanced"] as const)
              : arch.stage === "In Training"
              ? "Beginner"
              : randomItem([...PROFICIENCY_LEVELS]);
          const hours =
            profLevel === "Advanced" ? 300 + Math.floor(Math.random() * 500) :
            profLevel === "Intermediate" ? 100 + Math.floor(Math.random() * 200) :
            Math.floor(Math.random() * 100);
          return {
            skill_name: name,
            proficiency_level: profLevel as Skill["proficiency_level"],
            training_hours_accumulated: hours,
            target_hours_intermediate: 100,
            target_hours_advanced: 300,
          };
        }
      );

      // Certs
      const certCount = arch.stage === "Deployed" ? { min: 2, max: 5 } : { min: 0, max: 3 };
      const certNames = randomSubset(CERT_NAMES, certCount.min, certCount.max);
      const certifications: Certification[] = certNames.map((name) => {
        const issueDate = new Date(
          2023 + Math.floor(Math.random() * 3),
          Math.floor(Math.random() * 12),
          1 + Math.floor(Math.random() * 28)
        );
        const expiryDate = new Date(issueDate);
        expiryDate.setFullYear(expiryDate.getFullYear() + 2);
        const now = new Date();
        let status: Certification["status"] = "Active";
        if (expiryDate < now) status = "Expired";
        else if (expiryDate.getTime() - now.getTime() < 60 * 24 * 60 * 60 * 1000) status = "Expiring Soon";
        if (arch.status === "Missing Cert" && Math.random() > 0.5) status = "Expired";
        return {
          cert_name: name,
          issue_date: issueDate.toISOString().split("T")[0],
          expiry_date: expiryDate.toISOString().split("T")[0],
          status,
        };
      });

      // Documents
      const docs: TechDocument[] = DOC_TYPES.slice(0, 4 + Math.floor(Math.random() * 4)).map(
        (docType) => {
          let verStatus: TechDocument["verification_status"] = "Verified";
          if (arch.status === "Missing Docs" && Math.random() > 0.4) {
            verStatus = randomItem(["Not Submitted", "Pending Review"]);
          } else if (arch.stage === "Sourced") {
            verStatus = randomItem(["Not Submitted", "Pending Review", "Verified"]);
          }
          return { doc_type: docType, verification_status: verStatus };
        }
      );

      // Available from date
      const availFrom = new Date();
      if (arch.status === "Currently Assigned") {
        availFrom.setDate(availFrom.getDate() + 30 + Math.floor(Math.random() * 60));
      } else if (arch.status === "Rolling Off Soon") {
        availFrom.setDate(availFrom.getDate() + Math.floor(Math.random() * 14));
      } else if (arch.status === "Ready Now") {
        availFrom.setDate(availFrom.getDate() - Math.floor(Math.random() * 30));
      }

      // Site badges for deployed techs
      const siteBadges: string[] = [];
      const milestoneBadges: string[] = [];
      if (arch.stage === "Deployed") {
        if (Math.random() > 0.5) siteBadges.push("AT&T Cleared");
        if (Math.random() > 0.6) siteBadges.push("Google DC Cleared");
        if (Math.random() > 0.7) siteBadges.push("AWS Facility Access");
        if (skills.some(s => s.training_hours_accumulated >= 500)) milestoneBadges.push("500+ Hours");
        if (Math.random() > 0.4) milestoneBadges.push("5+ Projects");
        if (Math.random() > 0.7) milestoneBadges.push("Zero Safety Incidents");
      }

      techs.push({
        id: generateId(),
        name: `${firstName} ${lastName}`,
        email: `${firstName.toLowerCase()}.${lastName.toLowerCase()}@email.com`,
        phone: `(${555 + (idx % 10)}) ${100 + idx}-${1000 + idx}`,
        home_base_city: city,
        approved_regions: regions,
        career_stage: arch.stage,
        deployability_status: arch.status,
        deployability_locked: arch.status === "Inactive",
        available_from: availFrom.toISOString().split("T")[0],
        skills,
        certifications,
        documents: docs,
        site_badges: siteBadges,
        milestone_badges: milestoneBadges,
        avatar_url: undefined,
      });
    }
  }

  return techs;
}

// ---- Store ----

export interface TechnicianFilters {
  search: string;
  career_stage: string;
  deployability_status: string;
  region: string;
  skill: string;
  available_before: string;
}

interface TechnicianState {
  technicians: Technician[];
  selectedTechnician: Technician | null;
  filters: TechnicianFilters;
  isLoading: boolean;
  totalCount: number;
  page: number;
  pageSize: number;
  viewMode: "table" | "cards";

  // Actions
  setFilters: (filters: Partial<TechnicianFilters>) => void;
  resetFilters: () => void;
  setPage: (page: number) => void;
  setViewMode: (mode: "table" | "cards") => void;
  fetchTechnicians: () => Promise<void>;
  fetchTechnician: (id: string) => Promise<void>;
  updateTechnician: (id: string, data: Partial<Technician>) => Promise<void>;
  addSkill: (techId: string, skill: Omit<Skill, "target_hours_intermediate" | "target_hours_advanced">) => Promise<void>;
  removeSkill: (techId: string, skillName: string) => Promise<void>;
  updateSkill: (techId: string, skillName: string, updates: Partial<Skill>) => Promise<void>;
  addCertification: (techId: string, cert: Certification) => Promise<void>;
  removeCertification: (techId: string, certName: string) => Promise<void>;
  addDocument: (techId: string, doc: TechDocument) => Promise<void>;
  updateDocument: (techId: string, docType: string, updates: Partial<TechDocument>) => Promise<void>;
  addBadge: (techId: string, badge: string, type: "site" | "milestone") => Promise<void>;
  removeBadge: (techId: string, badge: string, type: "site" | "milestone") => Promise<void>;
  initialize: () => void;
}

const DEFAULT_FILTERS: TechnicianFilters = {
  search: "",
  career_stage: "",
  deployability_status: "",
  region: "",
  skill: "",
  available_before: "",
};

// Generate mock data once
let _mockTechnicians: Technician[] | null = null;
function getMockTechnicians(): Technician[] {
  if (!_mockTechnicians) {
    _mockTechnicians = generateMockTechnicians(52);
  }
  return _mockTechnicians;
}

export const useTechnicianStore = create<TechnicianState>((set, get) => ({
  technicians: [],
  selectedTechnician: null,
  filters: { ...DEFAULT_FILTERS },
  isLoading: false,
  totalCount: 0,
  page: 1,
  pageSize: 20,
  viewMode: "table",

  setFilters: (newFilters) => {
    set((state) => ({
      filters: { ...state.filters, ...newFilters },
      page: 1,
    }));
    get().fetchTechnicians();
  },

  resetFilters: () => {
    set({ filters: { ...DEFAULT_FILTERS }, page: 1 });
    get().fetchTechnicians();
  },

  setPage: (page) => {
    set({ page });
    get().fetchTechnicians();
  },

  setViewMode: (mode) => set({ viewMode: mode }),

  fetchTechnicians: async () => {
    const { filters, page, pageSize } = get();
    set({ isLoading: true });

    try {
      const params: Record<string, string | number> = {
        skip: (page - 1) * pageSize,
        limit: pageSize,
      };
      if (filters.search) params.search = filters.search;
      if (filters.career_stage) params.career_stage = filters.career_stage;
      if (filters.deployability_status) params.deployability_status = filters.deployability_status;
      if (filters.region) params.region = filters.region;
      if (filters.skill) params.skills = filters.skill;
      if (filters.available_before) params.available_from = filters.available_before;

      const res = await api.get("/technicians", { params });
      const data = res.data;

      // Map backend response to frontend types
      const techs: Technician[] = data.items.map((t: Record<string, unknown>) => ({
        id: t.id,
        name: `${t.first_name} ${t.last_name}`,
        email: t.email,
        phone: t.phone,
        home_base_city: t.home_base_city,
        approved_regions: t.approved_regions || [],
        career_stage: t.career_stage,
        deployability_status: t.deployability_status,
        deployability_locked: t.deployability_locked || false,
        available_from: t.available_from || "",
        skills: ((t.skills as Array<Record<string, unknown>>) || []).map((s) => ({
          skill_name: s.skill_name,
          proficiency_level: s.proficiency_level,
          training_hours_accumulated: s.training_hours_accumulated || 0,
          target_hours_intermediate: 100,
          target_hours_advanced: 300,
        })),
        certifications: ((t.certifications as Array<Record<string, unknown>>) || []).map((c) => ({
          cert_name: c.cert_name,
          issue_date: c.issue_date || "",
          expiry_date: c.expiry_date || "",
          status: c.status || "Pending",
        })),
        documents: ((t.documents as Array<Record<string, unknown>>) || []).map((d) => ({
          doc_type: d.doc_type,
          verification_status: d.verification_status || "Not Submitted",
        })),
        site_badges: ((t.badges as Array<Record<string, unknown>>) || [])
          .filter((b) => b.badge_type === "site")
          .map((b) => b.badge_name as string),
        milestone_badges: ((t.badges as Array<Record<string, unknown>>) || [])
          .filter((b) => b.badge_type === "milestone")
          .map((b) => b.badge_name as string),
        avatar_url: t.avatar_url as string | undefined,
        ops_notes: t.ops_notes as string | undefined,
        years_experience: t.years_experience as number | undefined,
        total_project_count: t.total_project_count as number | undefined,
        total_approved_hours: t.total_approved_hours as number | undefined,
        hire_date: t.hire_date as string | undefined,
      }));

      set({ technicians: techs, totalCount: data.total, isLoading: false });
    } catch {
      // Fallback to mock data
      const allTechs = getMockTechnicians();
      let filtered = [...allTechs];

      if (filters.search) {
        const q = filters.search.toLowerCase();
        filtered = filtered.filter(
          (t) =>
            t.name.toLowerCase().includes(q) ||
            (t.email && t.email.toLowerCase().includes(q))
        );
      }
      if (filters.career_stage) {
        filtered = filtered.filter((t) => t.career_stage === filters.career_stage);
      }
      if (filters.deployability_status) {
        filtered = filtered.filter(
          (t) => t.deployability_status === filters.deployability_status
        );
      }
      if (filters.region) {
        filtered = filtered.filter((t) =>
          t.approved_regions.includes(filters.region)
        );
      }
      if (filters.skill) {
        filtered = filtered.filter((t) =>
          t.skills.some((s) =>
            s.skill_name.toLowerCase().includes(filters.skill.toLowerCase())
          )
        );
      }
      if (filters.available_before) {
        const cutoff = new Date(filters.available_before);
        filtered = filtered.filter((t) => new Date(t.available_from) <= cutoff);
      }

      const start = (page - 1) * pageSize;
      const paged = filtered.slice(start, start + pageSize);

      set({
        technicians: paged,
        totalCount: filtered.length,
        isLoading: false,
      });
    }
  },

  fetchTechnician: async (id) => {
    set({ isLoading: true });
    try {
      const res = await api.get(`/technicians/${id}`);
      const t = res.data;
      const tech: Technician = {
        id: t.id,
        name: `${t.first_name} ${t.last_name}`,
        email: t.email,
        phone: t.phone,
        home_base_city: t.home_base_city,
        approved_regions: t.approved_regions || [],
        career_stage: t.career_stage,
        deployability_status: t.deployability_status,
        deployability_locked: t.deployability_locked || false,
        available_from: t.available_from || "",
        skills: (t.skills || []).map((s: Record<string, unknown>) => ({
          skill_name: s.skill_name,
          proficiency_level: s.proficiency_level,
          training_hours_accumulated: s.training_hours_accumulated || 0,
          target_hours_intermediate: 100,
          target_hours_advanced: 300,
        })),
        certifications: (t.certifications || []).map((c: Record<string, unknown>) => ({
          cert_name: c.cert_name,
          issue_date: c.issue_date || "",
          expiry_date: c.expiry_date || "",
          status: c.status || "Pending",
        })),
        documents: (t.documents || []).map((d: Record<string, unknown>) => ({
          doc_type: d.doc_type,
          verification_status: d.verification_status || "Not Submitted",
        })),
        site_badges: (t.badges || [])
          .filter((b: Record<string, unknown>) => b.badge_type === "site")
          .map((b: Record<string, unknown>) => b.badge_name as string),
        milestone_badges: (t.badges || [])
          .filter((b: Record<string, unknown>) => b.badge_type === "milestone")
          .map((b: Record<string, unknown>) => b.badge_name as string),
        avatar_url: t.avatar_url,
        ops_notes: t.ops_notes,
        years_experience: t.years_experience,
        total_project_count: t.total_project_count,
        total_approved_hours: t.total_approved_hours,
        hire_date: t.hire_date,
      };
      set({ selectedTechnician: tech, isLoading: false });
    } catch {
      // Fallback to mock
      const allTechs = getMockTechnicians();
      const tech = allTechs.find((t) => t.id === id) || null;
      set({ selectedTechnician: tech, isLoading: false });
    }
  },

  updateTechnician: async (id, data) => {
    try {
      await api.patch(`/technicians/${id}`, data);
      await get().fetchTechnician(id);
    } catch {
      // Update locally in mock mode
      const allTechs = getMockTechnicians();
      const idx = allTechs.findIndex((t) => t.id === id);
      if (idx !== -1) {
        allTechs[idx] = { ...allTechs[idx], ...data };
        set({ selectedTechnician: { ...allTechs[idx] } });
      }
    }
  },

  addSkill: async (techId, skill) => {
    try {
      await api.post(`/technicians/${techId}/skills`, skill);
      await get().fetchTechnician(techId);
    } catch {
      const allTechs = getMockTechnicians();
      const tech = allTechs.find((t) => t.id === techId);
      if (tech) {
        tech.skills.push({
          ...skill,
          target_hours_intermediate: 100,
          target_hours_advanced: 300,
        });
        set({ selectedTechnician: { ...tech } });
      }
    }
  },

  removeSkill: async (techId, skillName) => {
    try {
      // Find skill ID first
      const tech = get().selectedTechnician;
      if (tech) {
        await api.delete(`/technicians/${techId}/skills/${skillName}`);
        await get().fetchTechnician(techId);
      }
    } catch {
      const allTechs = getMockTechnicians();
      const tech = allTechs.find((t) => t.id === techId);
      if (tech) {
        tech.skills = tech.skills.filter((s) => s.skill_name !== skillName);
        set({ selectedTechnician: { ...tech } });
      }
    }
  },

  updateSkill: async (techId, skillName, updates) => {
    try {
      await api.patch(`/technicians/${techId}/skills/${skillName}`, updates);
      await get().fetchTechnician(techId);
    } catch {
      const allTechs = getMockTechnicians();
      const tech = allTechs.find((t) => t.id === techId);
      if (tech) {
        const skill = tech.skills.find((s) => s.skill_name === skillName);
        if (skill) Object.assign(skill, updates);
        set({ selectedTechnician: { ...tech } });
      }
    }
  },

  addCertification: async (techId, cert) => {
    try {
      await api.post(`/technicians/${techId}/certifications`, cert);
      await get().fetchTechnician(techId);
    } catch {
      const allTechs = getMockTechnicians();
      const tech = allTechs.find((t) => t.id === techId);
      if (tech) {
        tech.certifications.push(cert);
        set({ selectedTechnician: { ...tech } });
      }
    }
  },

  removeCertification: async (techId, certName) => {
    try {
      await api.delete(`/technicians/${techId}/certifications/${certName}`);
      await get().fetchTechnician(techId);
    } catch {
      const allTechs = getMockTechnicians();
      const tech = allTechs.find((t) => t.id === techId);
      if (tech) {
        tech.certifications = tech.certifications.filter((c) => c.cert_name !== certName);
        set({ selectedTechnician: { ...tech } });
      }
    }
  },

  addDocument: async (techId, doc) => {
    try {
      await api.post(`/technicians/${techId}/documents`, doc);
      await get().fetchTechnician(techId);
    } catch {
      const allTechs = getMockTechnicians();
      const tech = allTechs.find((t) => t.id === techId);
      if (tech) {
        tech.documents.push(doc);
        set({ selectedTechnician: { ...tech } });
      }
    }
  },

  updateDocument: async (techId, docType, updates) => {
    try {
      await api.patch(`/technicians/${techId}/documents/${docType}`, updates);
      await get().fetchTechnician(techId);
    } catch {
      const allTechs = getMockTechnicians();
      const tech = allTechs.find((t) => t.id === techId);
      if (tech) {
        const doc = tech.documents.find((d) => d.doc_type === docType);
        if (doc) Object.assign(doc, updates);
        set({ selectedTechnician: { ...tech } });
      }
    }
  },

  addBadge: async (techId, badge, type) => {
    try {
      await api.post(`/technicians/${techId}/badges`, {
        badge_type: type,
        badge_name: badge,
      });
      await get().fetchTechnician(techId);
    } catch {
      const allTechs = getMockTechnicians();
      const tech = allTechs.find((t) => t.id === techId);
      if (tech) {
        if (type === "site") tech.site_badges.push(badge);
        else tech.milestone_badges.push(badge);
        set({ selectedTechnician: { ...tech } });
      }
    }
  },

  removeBadge: async (techId, badge, type) => {
    try {
      await api.delete(`/technicians/${techId}/badges/${badge}`);
      await get().fetchTechnician(techId);
    } catch {
      const allTechs = getMockTechnicians();
      const tech = allTechs.find((t) => t.id === techId);
      if (tech) {
        if (type === "site") {
          tech.site_badges = tech.site_badges.filter((b) => b !== badge);
        } else {
          tech.milestone_badges = tech.milestone_badges.filter((b) => b !== badge);
        }
        set({ selectedTechnician: { ...tech } });
      }
    }
  },

  initialize: () => {
    get().fetchTechnicians();
  },
}));

// Export for use in other components
export { SKILLS_TAXONOMY, CERT_NAMES, DOC_TYPES, REGIONS };
