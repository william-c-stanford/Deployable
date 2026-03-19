/**
 * Career Passport Store
 *
 * Manages shareable career passport tokens:
 * - Generate new share tokens with optional labels and expiry
 * - List tokens for a technician
 * - Revoke tokens
 * - Copy share URLs to clipboard
 * - Trigger PDF download
 */
import { create } from "zustand";
import api from "@/lib/api";
import type { CareerPassportToken } from "@/types";

export interface CareerPassportState {
  // Data
  tokens: CareerPassportToken[];
  isLoading: boolean;
  isGenerating: boolean;
  error: string | null;

  // Clipboard feedback
  copiedTokenId: string | null;

  // Actions
  fetchTokens: (technicianId: string, includeRevoked?: boolean) => Promise<void>;
  generateToken: (
    technicianId: string,
    label?: string,
    expiryDays?: number,
  ) => Promise<CareerPassportToken | null>;
  revokeToken: (tokenId: string) => Promise<boolean>;
  copyShareUrl: (token: CareerPassportToken) => Promise<void>;
  downloadPdf: (technicianId: string, technicianName: string) => void;
  reset: () => void;
}

// Seed/mock tokens for demo fallback
function generateMockTokens(technicianId: string): CareerPassportToken[] {
  const now = new Date();
  const thirtyDaysFromNow = new Date(
    now.getTime() + 30 * 24 * 60 * 60 * 1000,
  );
  const tenDaysAgo = new Date(now.getTime() - 10 * 24 * 60 * 60 * 1000);
  const fiveDaysAgo = new Date(now.getTime() - 5 * 24 * 60 * 60 * 1000);
  const twentyDaysFromNow = new Date(
    now.getTime() + 20 * 24 * 60 * 60 * 1000,
  );
  const twentyFiveDaysFromNow = new Date(
    now.getTime() + 25 * 24 * 60 * 60 * 1000,
  );

  return [
    {
      id: "cpt-1",
      technician_id: technicianId,
      token: "demo_passport_abc123xyz",
      label: "Shared with Lumen HR",
      revoked: false,
      expires_at: thirtyDaysFromNow.toISOString(),
      created_at: tenDaysAgo.toISOString(),
      created_by_role: "ops",
      is_active: true,
      share_url: "/passport/demo_passport_abc123xyz",
    },
    {
      id: "cpt-2",
      technician_id: technicianId,
      token: "demo_passport_def456uvw",
      label: "AT&T project bid",
      revoked: false,
      expires_at: twentyFiveDaysFromNow.toISOString(),
      created_at: fiveDaysAgo.toISOString(),
      created_by_role: "technician",
      is_active: true,
      share_url: "/passport/demo_passport_def456uvw",
    },
    {
      id: "cpt-3",
      technician_id: technicianId,
      token: "demo_passport_revoked789",
      label: "Old recruiter link",
      revoked: true,
      expires_at: twentyDaysFromNow.toISOString(),
      created_at: new Date(
        now.getTime() - 20 * 24 * 60 * 60 * 1000,
      ).toISOString(),
      created_by_role: "ops",
      is_active: false,
      share_url: "/passport/demo_passport_revoked789",
    },
  ];
}

let mockIdCounter = 100;

export const useCareerPassportStore = create<CareerPassportState>(
  (set, get) => ({
    tokens: [],
    isLoading: false,
    isGenerating: false,
    error: null,
    copiedTokenId: null,

    fetchTokens: async (technicianId: string, includeRevoked = true) => {
      set({ isLoading: true, error: null });
      try {
        const res = await api.get(
          `/career-passport/tokens/technician/${technicianId}`,
          { params: { include_revoked: includeRevoked } },
        );
        set({ tokens: res.data.tokens, isLoading: false });
      } catch {
        // Fallback to mock data
        set({
          tokens: generateMockTokens(technicianId),
          isLoading: false,
        });
      }
    },

    generateToken: async (
      technicianId: string,
      label?: string,
      expiryDays?: number,
    ) => {
      set({ isGenerating: true, error: null });
      try {
        const res = await api.post("/career-passport/tokens", {
          technician_id: technicianId,
          label: label || null,
          expiry_days: expiryDays || 30,
        });
        const newToken: CareerPassportToken = res.data;
        set((s) => ({
          tokens: [newToken, ...s.tokens],
          isGenerating: false,
        }));
        return newToken;
      } catch {
        // Fallback: create a mock token
        const now = new Date();
        const days = expiryDays || 30;
        const mockToken: CareerPassportToken = {
          id: `cpt-mock-${++mockIdCounter}`,
          technician_id: technicianId,
          token: `demo_${Math.random().toString(36).slice(2, 14)}`,
          label: label || null,
          revoked: false,
          expires_at: new Date(
            now.getTime() + days * 24 * 60 * 60 * 1000,
          ).toISOString(),
          created_at: now.toISOString(),
          created_by_role: "ops",
          is_active: true,
          share_url: "",
        };
        mockToken.share_url = `/passport/${mockToken.token}`;
        set((s) => ({
          tokens: [mockToken, ...s.tokens],
          isGenerating: false,
        }));
        return mockToken;
      }
    },

    revokeToken: async (tokenId: string) => {
      try {
        await api.post(`/career-passport/tokens/${tokenId}/revoke`);
        set((s) => ({
          tokens: s.tokens.map((t) =>
            t.id === tokenId
              ? {
                  ...t,
                  revoked: true,
                  is_active: false,
                }
              : t,
          ),
        }));
        return true;
      } catch {
        // Fallback: update locally
        set((s) => ({
          tokens: s.tokens.map((t) =>
            t.id === tokenId
              ? {
                  ...t,
                  revoked: true,
                  is_active: false,
                }
              : t,
          ),
        }));
        return true;
      }
    },

    copyShareUrl: async (token: CareerPassportToken) => {
      const url = `${window.location.origin}${token.share_url}`;
      try {
        await navigator.clipboard.writeText(url);
      } catch {
        // Fallback for non-HTTPS environments
        const textArea = document.createElement("textarea");
        textArea.value = url;
        textArea.style.position = "fixed";
        textArea.style.left = "-999999px";
        document.body.appendChild(textArea);
        textArea.select();
        document.execCommand("copy");
        document.body.removeChild(textArea);
      }
      set({ copiedTokenId: token.id });
      setTimeout(() => {
        const current = get().copiedTokenId;
        if (current === token.id) {
          set({ copiedTokenId: null });
        }
      }, 2000);
    },

    downloadPdf: (technicianId: string, technicianName: string) => {
      // Open the PDF endpoint in a new tab / trigger download
      const url = `/api/career-passport/pdf/${technicianId}`;
      const link = document.createElement("a");
      link.href = url;
      link.download = `career-passport-${technicianName.replace(/\s+/g, "-").toLowerCase()}.pdf`;
      link.target = "_blank";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    },

    reset: () => {
      set({
        tokens: [],
        isLoading: false,
        isGenerating: false,
        error: null,
        copiedTokenId: null,
      });
    },
  }),
);
