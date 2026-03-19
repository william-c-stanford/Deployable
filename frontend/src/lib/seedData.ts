import type { Technician, CareerStage, ProficiencyLevel, DeployabilityStatus } from '@/types';

const SKILLS_TAXONOMY = [
  'Fiber Splicing',
  'OTDR Testing',
  'Cable Pulling',
  'Aerial Installation',
  'Underground Conduit',
  'Network Design',
  'OSP Engineering',
  'Data Center Cabling',
  'Rack & Stack',
  'Power Systems',
  'Safety Compliance',
  'Project Management',
  'Microduct Installation',
  'Ribbon Fiber',
  'FTTH Installation',
  'Wireless Backhaul',
];

const CERTIFICATIONS = [
  'FOA CFOT',
  'FOA CFOS/S',
  'FOA CFOS/T',
  'BICSI Installer 1',
  'BICSI Installer 2',
  'BICSI RCDD',
  'OSHA 10',
  'OSHA 30',
  'CPR/First Aid',
  'CDL Class A',
  'Confined Space Entry',
  'Aerial Lift Certification',
];

const CITIES = [
  'Dallas, TX', 'Austin, TX', 'Phoenix, AZ', 'Denver, CO', 'Atlanta, GA',
  'Nashville, TN', 'Charlotte, NC', 'Columbus, OH', 'Tampa, FL', 'Portland, OR',
  'Salt Lake City, UT', 'Kansas City, MO', 'Raleigh, NC', 'Indianapolis, IN',
  'San Antonio, TX', 'Jacksonville, FL', 'Oklahoma City, OK', 'Boise, ID',
];

const REGIONS = [
  'TX', 'AZ', 'CO', 'GA', 'TN', 'NC', 'OH', 'FL', 'OR', 'UT', 'MO', 'IN', 'OK', 'ID',
];

const FIRST_NAMES = [
  'Marcus', 'Sarah', 'James', 'Elena', 'DeShawn', 'Maria', 'Tyler', 'Aisha',
  'Brandon', 'Jennifer', 'Carlos', 'Brittany', 'Andre', 'Keisha', 'Ryan',
  'Samantha', 'Miguel', 'Taylor', 'David', 'Jasmine', 'Kevin', 'Rachel',
  'Anthony', 'Nicole', 'Robert', 'Amanda', 'Christopher', 'Michelle',
  'Joseph', 'Stephanie', 'Daniel', 'Priya', 'Hassan', 'Yuki', 'Wei',
  'Olga', 'Erik', 'Fatima', 'Tomasz', 'Ling', 'Connor', 'Zara',
  'Gabriel', 'Aaliyah', 'Nathan', 'Sofia', 'Isaiah', 'Megan', 'Ethan',
  'Hannah', 'Lucas', 'Destiny',
];

const LAST_NAMES = [
  'Johnson', 'Williams', 'Rodriguez', 'Chen', 'Davis', 'Martinez', 'Thompson',
  'Garcia', 'Jackson', 'White', 'Harris', 'Clark', 'Lewis', 'Robinson',
  'Walker', 'Young', 'Allen', 'King', 'Wright', 'Scott', 'Hill',
  'Green', 'Adams', 'Baker', 'Nelson', 'Carter', 'Mitchell', 'Perez',
  'Roberts', 'Turner', 'Phillips', 'Campbell', 'Parker', 'Evans', 'Edwards',
  'Collins', 'Stewart', 'Sanchez', 'Morris', 'Rogers', 'Reed', 'Cook',
  'Morgan', 'Bell', 'Murphy', 'Bailey', 'Rivera', 'Cooper', 'Richardson',
  'Cox', 'Howard', 'Ward',
];

// Seeded random for deterministic output
function seededRandom(seed: number) {
  let s = seed;
  return () => {
    s = (s * 16807 + 0) % 2147483647;
    return (s - 1) / 2147483646;
  };
}

const rand = seededRandom(42);

function randomFrom<T>(arr: T[]): T {
  return arr[Math.floor(rand() * arr.length)];
}

function randomSubset<T>(arr: T[], min: number, max: number): T[] {
  const count = Math.floor(rand() * (max - min + 1)) + min;
  const shuffled = [...arr].sort(() => rand() - 0.5);
  return shuffled.slice(0, count);
}

function generateSkills(stage: CareerStage) {
  const numSkills = stage === 'Sourced' ? 2 :
    stage === 'Screened' ? 3 :
    stage === 'In Training' ? 4 :
    stage === 'Training Completed' ? 5 :
    stage === 'Awaiting Assignment' ? 5 : 6;

  const selectedSkills = randomSubset(SKILLS_TAXONOMY, Math.max(2, numSkills - 1), numSkills + 1);

  return selectedSkills.map((skill_name) => {
    let level: ProficiencyLevel;
    let hours: number;

    if (stage === 'Sourced' || stage === 'Screened') {
      level = 'Beginner';
      hours = Math.floor(rand() * 50);
    } else if (stage === 'In Training') {
      const r = rand();
      if (r < 0.5) {
        level = 'Beginner';
        hours = Math.floor(rand() * 99) + 1;
      } else {
        level = 'Intermediate';
        hours = Math.floor(rand() * 100) + 100;
      }
    } else {
      const r = rand();
      if (r < 0.2) {
        level = 'Beginner';
        hours = Math.floor(rand() * 80) + 20;
      } else if (r < 0.6) {
        level = 'Intermediate';
        hours = Math.floor(rand() * 200) + 100;
      } else {
        level = 'Advanced';
        hours = Math.floor(rand() * 500) + 300;
      }
    }

    return {
      skill_name,
      proficiency_level: level,
      training_hours_accumulated: hours,
      target_hours_intermediate: 100,
      target_hours_advanced: 300,
    };
  });
}

function generateCerts(stage: CareerStage) {
  if (stage === 'Sourced') return [];
  const numCerts = stage === 'Screened' ? 1 : stage === 'In Training' ? 2 : 3;
  const selected = randomSubset(CERTIFICATIONS, Math.max(1, numCerts - 1), numCerts + 1);
  return selected.map((cert_name) => {
    const month = Math.floor(rand() * 12);
    const day = Math.floor(rand() * 28) + 1;
    const issued = new Date(2024, month, day);
    const expiry = new Date(issued);
    expiry.setFullYear(expiry.getFullYear() + 2);
    const now = new Date(2026, 2, 19); // current date from context
    const daysUntilExpiry = (expiry.getTime() - now.getTime()) / (1000 * 60 * 60 * 24);
    return {
      cert_name,
      issue_date: issued.toISOString().split('T')[0],
      expiry_date: expiry.toISOString().split('T')[0],
      status: daysUntilExpiry < 0 ? 'Expired' as const :
        daysUntilExpiry < 90 ? 'Expiring Soon' as const : 'Active' as const,
    };
  });
}

const STAGE_DISTRIBUTION: CareerStage[] = [
  'Sourced', 'Sourced', 'Sourced', 'Sourced', 'Sourced',
  'Screened', 'Screened', 'Screened', 'Screened', 'Screened', 'Screened',
  'In Training', 'In Training', 'In Training', 'In Training', 'In Training',
  'In Training', 'In Training', 'In Training', 'In Training', 'In Training',
  'Training Completed', 'Training Completed', 'Training Completed', 'Training Completed',
  'Training Completed', 'Training Completed',
  'Awaiting Assignment', 'Awaiting Assignment', 'Awaiting Assignment', 'Awaiting Assignment',
  'Awaiting Assignment', 'Awaiting Assignment', 'Awaiting Assignment',
  'Deployed', 'Deployed', 'Deployed', 'Deployed', 'Deployed', 'Deployed',
  'Deployed', 'Deployed', 'Deployed', 'Deployed', 'Deployed', 'Deployed',
  'Deployed', 'Deployed', 'Deployed',
  'In Training', 'Screened', 'Awaiting Assignment', 'Deployed', 'In Training',
  'Training Completed', 'Sourced',
];

function getDeployabilityFromStage(stage: CareerStage, skills: ReturnType<typeof generateSkills>, certs: ReturnType<typeof generateCerts>): DeployabilityStatus {
  if (stage === 'In Training') return 'In Training';
  if (stage === 'Deployed') return 'Currently Assigned';
  if (stage === 'Sourced' || stage === 'Screened') return 'In Training';
  if (certs.some(c => c.status === 'Expired')) return 'Missing Cert';
  if (stage === 'Awaiting Assignment' || stage === 'Training Completed') {
    const hasAdvanced = skills.some(s => s.proficiency_level === 'Advanced');
    return hasAdvanced ? 'Ready Now' : 'In Training';
  }
  return 'Ready Now';
}

function generateSeedTechnicians(): Technician[] {
  const technicians: Technician[] = [];
  const usedNames = new Set<string>();

  for (let i = 0; i < STAGE_DISTRIBUTION.length; i++) {
    let name: string;
    do {
      name = `${randomFrom(FIRST_NAMES)} ${randomFrom(LAST_NAMES)}`;
    } while (usedNames.has(name));
    usedNames.add(name);

    const stage = STAGE_DISTRIBUTION[i];
    const city = randomFrom(CITIES);
    const cityState = city.split(', ')[1];
    const regions = [cityState, ...randomSubset(REGIONS.filter(r => r !== cityState), 1, 3)];
    const skills = generateSkills(stage);
    const certs = generateCerts(stage);

    const SITE_BADGES = ['AT&T Approved', 'Lumen Certified', 'Meta DC Cleared', 'Google Fiber Qualified'];
    const siteBadges = stage === 'Deployed' ? randomSubset(SITE_BADGES, 0, 2) : [];

    const totalHours = skills.reduce((sum, s) => sum + s.training_hours_accumulated, 0);
    const milestoneBadges: string[] = [];
    if (totalHours >= 500) milestoneBadges.push('500 Hour Club');
    if (totalHours >= 1000) milestoneBadges.push('Master Technician');
    if (skills.filter(s => s.proficiency_level === 'Advanced').length >= 3) {
      milestoneBadges.push('Triple Threat');
    }

    const tech: Technician = {
      id: `tech_${String(i + 1).padStart(3, '0')}`,
      name,
      email: `${name.toLowerCase().replace(' ', '.')}@email.com`,
      phone: `(${Math.floor(rand() * 900) + 100}) ${Math.floor(rand() * 900) + 100}-${Math.floor(rand() * 9000) + 1000}`,
      home_base_city: city,
      approved_regions: regions,
      career_stage: stage,
      deployability_status: getDeployabilityFromStage(stage, skills, certs),
      deployability_locked: rand() < 0.05,
      available_from: stage === 'Deployed'
        ? new Date(2026, 3 + Math.floor(rand() * 4), Math.floor(rand() * 28) + 1).toISOString().split('T')[0]
        : '2026-03-19',
      skills,
      certifications: certs,
      documents: [
        { doc_type: 'ID Verification', verification_status: rand() > 0.1 ? 'Verified' : 'Pending Review' },
        { doc_type: 'Background Check', verification_status: stage === 'Sourced' ? 'Not Submitted' : rand() > 0.2 ? 'Verified' : 'Pending Review' },
        { doc_type: 'Drug Screening', verification_status: ['Sourced', 'Screened'].includes(stage) ? 'Not Submitted' : 'Verified' },
      ],
      site_badges: siteBadges,
      milestone_badges: milestoneBadges,
    };

    technicians.push(tech);
  }

  return technicians;
}

// Cache - generate once
let _cached: Technician[] | null = null;
export function getSeedTechnicians(): Technician[] {
  if (!_cached) {
    _cached = generateSeedTechnicians();
  }
  return _cached;
}

export function getAllSkills(): string[] {
  return SKILLS_TAXONOMY;
}

export function getAllRegions(): string[] {
  return REGIONS;
}
