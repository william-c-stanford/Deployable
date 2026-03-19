export interface User {
  user_id: string;
  name: string;
  role: "ops" | "technician" | "partner";
  scoped_to?: string | null;
}

export interface DemoUser {
  id: string;
  name: string;
  role: string;
  scoped_to?: string | null;
}
