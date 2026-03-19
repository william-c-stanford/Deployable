/**
 * CareerPassportPanel
 *
 * Full-featured career passport management panel used in both:
 * - Ops TechnicianProfile (as a tab)
 * - Tech TechnicianPortal (as a section)
 *
 * Features:
 * - PDF download button
 * - Generate new shareable link with label + expiry
 * - Copy share URL to clipboard
 * - Token revocation management
 * - Active / revoked token list
 */
import { useEffect, useState } from "react";
import {
  Download,
  Link2,
  Copy,
  Check,
  Plus,
  XCircle,
  Clock,
  Shield,
  ExternalLink,
  AlertTriangle,
  Eye,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useCareerPassportStore } from "@/stores/careerPassportStore";
import { cn } from "@/lib/utils";
import type { CareerPassportToken } from "@/types";

// -------------------------------------------------------------------
// Props
// -------------------------------------------------------------------

interface CareerPassportPanelProps {
  technicianId: string;
  technicianName: string;
  /** Compact layout for technician portal */
  compact?: boolean;
  /** Role context */
  role?: "ops" | "technician";
}

// -------------------------------------------------------------------
// Helpers
// -------------------------------------------------------------------

function timeUntilExpiry(expiresAt: string): string {
  const diff = new Date(expiresAt).getTime() - Date.now();
  if (diff <= 0) return "Expired";
  const days = Math.floor(diff / (1000 * 60 * 60 * 24));
  if (days > 1) return `${days} days`;
  const hours = Math.floor(diff / (1000 * 60 * 60));
  if (hours > 1) return `${hours} hours`;
  const minutes = Math.floor(diff / (1000 * 60));
  return `${minutes} min`;
}

function formatTokenDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function getTokenStatusBadge(token: CareerPassportToken) {
  if (token.revoked) {
    return (
      <Badge variant="destructive" className="text-xs gap-1">
        <XCircle className="h-3 w-3" /> Revoked
      </Badge>
    );
  }
  const diff = new Date(token.expires_at).getTime() - Date.now();
  if (diff <= 0) {
    return (
      <Badge variant="destructive" className="text-xs gap-1">
        <Clock className="h-3 w-3" /> Expired
      </Badge>
    );
  }
  const days = Math.floor(diff / (1000 * 60 * 60 * 24));
  if (days <= 7) {
    return (
      <Badge variant="warning" className="text-xs gap-1">
        <Clock className="h-3 w-3" /> Expiring soon
      </Badge>
    );
  }
  return (
    <Badge variant="success" className="text-xs gap-1">
      <Check className="h-3 w-3" /> Active
    </Badge>
  );
}

// -------------------------------------------------------------------
// Generate Token Dialog
// -------------------------------------------------------------------

function GenerateTokenDialog({
  technicianId,
  onGenerated,
}: {
  technicianId: string;
  onGenerated?: (token: CareerPassportToken) => void;
}) {
  const { generateToken, isGenerating } = useCareerPassportStore();
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [expiryDays, setExpiryDays] = useState("30");

  const handleGenerate = async () => {
    const token = await generateToken(
      technicianId,
      label || undefined,
      parseInt(expiryDays, 10),
    );
    if (token) {
      onGenerated?.(token);
      setLabel("");
      setExpiryDays("30");
      setOpen(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" className="gap-1.5">
          <Plus className="h-4 w-4" />
          Generate Link
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Link2 className="h-5 w-5 text-primary" />
            Generate Shareable Link
          </DialogTitle>
          <DialogDescription>
            Create a shareable career passport link. Recipients can view verified skills, certifications, and project history.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-2">
          <div>
            <Label htmlFor="link-label">Label (optional)</Label>
            <Input
              id="link-label"
              placeholder="e.g., Shared with Lumen HR"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              className="mt-1"
            />
            <p className="text-xs text-muted-foreground mt-1">
              Help you remember who this link was shared with
            </p>
          </div>
          <div>
            <Label htmlFor="link-expiry">Expires in</Label>
            <Select value={expiryDays} onValueChange={setExpiryDays}>
              <SelectTrigger className="mt-1">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="7">7 days</SelectItem>
                <SelectItem value="14">14 days</SelectItem>
                <SelectItem value="30">30 days</SelectItem>
                <SelectItem value="60">60 days</SelectItem>
                <SelectItem value="90">90 days</SelectItem>
                <SelectItem value="180">180 days</SelectItem>
                <SelectItem value="365">1 year</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button onClick={handleGenerate} disabled={isGenerating}>
            {isGenerating ? (
              <>
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent mr-2" />
                Generating...
              </>
            ) : (
              <>
                <Link2 className="h-4 w-4 mr-1" />
                Generate
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// -------------------------------------------------------------------
// Revoke Confirmation Dialog
// -------------------------------------------------------------------

function RevokeDialog({
  token,
  onConfirm,
}: {
  token: CareerPassportToken;
  onConfirm: () => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="text-destructive hover:text-destructive hover:bg-destructive/10 gap-1 h-8 px-2"
        >
          <XCircle className="h-3.5 w-3.5" />
          Revoke
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-destructive">
            <AlertTriangle className="h-5 w-5" />
            Revoke Share Link
          </DialogTitle>
          <DialogDescription>
            This will permanently deactivate this shareable link. Anyone with this URL will no longer be able to view the career passport.
          </DialogDescription>
        </DialogHeader>
        <div className="py-2">
          <div className="p-3 rounded-lg bg-muted/50 border">
            <p className="text-sm font-medium">{token.label || "Unlabeled link"}</p>
            <p className="text-xs text-muted-foreground mt-1">
              Created {formatTokenDate(token.created_at)} · Expires{" "}
              {formatTokenDate(token.expires_at)}
            </p>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => {
              onConfirm();
              setOpen(false);
            }}
          >
            <XCircle className="h-4 w-4 mr-1" />
            Revoke Link
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// -------------------------------------------------------------------
// Token Row
// -------------------------------------------------------------------

function TokenRow({ token }: { token: CareerPassportToken }) {
  const { copyShareUrl, revokeToken, copiedTokenId } = useCareerPassportStore();
  const isCopied = copiedTokenId === token.id;

  return (
    <TableRow
      className={cn(
        token.revoked && "opacity-50",
        !token.is_active && !token.revoked && "opacity-60",
      )}
    >
      <TableCell>
        <div className="min-w-0">
          <p className="text-sm font-medium truncate">
            {token.label || "Unlabeled link"}
          </p>
          <p className="text-xs text-muted-foreground font-mono truncate max-w-[200px]">
            ...{token.token.slice(-12)}
          </p>
        </div>
      </TableCell>
      <TableCell>{getTokenStatusBadge(token)}</TableCell>
      <TableCell className="text-xs text-muted-foreground">
        {formatTokenDate(token.created_at)}
      </TableCell>
      <TableCell className="text-xs text-muted-foreground">
        {token.revoked ? (
          <span className="text-destructive">Revoked</span>
        ) : (
          timeUntilExpiry(token.expires_at)
        )}
      </TableCell>
      <TableCell className="text-xs text-muted-foreground capitalize">
        {token.created_by_role}
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-1">
          {token.is_active && (
            <>
              <Button
                variant="ghost"
                size="sm"
                className="h-8 px-2 gap-1"
                onClick={() => copyShareUrl(token)}
              >
                {isCopied ? (
                  <>
                    <Check className="h-3.5 w-3.5 text-emerald-500" />
                    <span className="text-xs text-emerald-500">Copied!</span>
                  </>
                ) : (
                  <>
                    <Copy className="h-3.5 w-3.5" />
                    <span className="text-xs">Copy</span>
                  </>
                )}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-8 px-2 gap-1"
                onClick={() =>
                  window.open(token.share_url || "", "_blank")
                }
              >
                <ExternalLink className="h-3.5 w-3.5" />
                <span className="text-xs">Open</span>
              </Button>
              <RevokeDialog
                token={token}
                onConfirm={() => revokeToken(token.id)}
              />
            </>
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}

// -------------------------------------------------------------------
// Compact Token Card (for mobile / tech portal)
// -------------------------------------------------------------------

function CompactTokenCard({ token }: { token: CareerPassportToken }) {
  const { copyShareUrl, revokeToken, copiedTokenId } = useCareerPassportStore();
  const isCopied = copiedTokenId === token.id;

  return (
    <div
      className={cn(
        "p-3 rounded-lg border bg-card",
        token.revoked && "opacity-50",
        !token.is_active && !token.revoked && "opacity-60",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium truncate">
              {token.label || "Unlabeled link"}
            </p>
            {getTokenStatusBadge(token)}
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            Created {formatTokenDate(token.created_at)} · By{" "}
            <span className="capitalize">{token.created_by_role}</span>
          </p>
          {!token.revoked && (
            <p className="text-xs text-muted-foreground">
              Expires in {timeUntilExpiry(token.expires_at)}
            </p>
          )}
        </div>
      </div>
      {token.is_active && (
        <div className="flex items-center gap-2 mt-3 pt-2 border-t border-border">
          <Button
            variant="outline"
            size="sm"
            className="h-8 gap-1.5 flex-1 touch-manipulation"
            onClick={() => copyShareUrl(token)}
          >
            {isCopied ? (
              <>
                <Check className="h-3.5 w-3.5 text-emerald-500" />
                Copied!
              </>
            ) : (
              <>
                <Copy className="h-3.5 w-3.5" />
                Copy Link
              </>
            )}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-8 px-2"
            onClick={() => window.open(token.share_url || "", "_blank")}
          >
            <ExternalLink className="h-3.5 w-3.5" />
          </Button>
          <RevokeDialog
            token={token}
            onConfirm={() => revokeToken(token.id)}
          />
        </div>
      )}
    </div>
  );
}

// -------------------------------------------------------------------
// Main Panel Component
// -------------------------------------------------------------------

export function CareerPassportPanel({
  technicianId,
  technicianName,
  compact = false,
  role = "ops",
}: CareerPassportPanelProps) {
  const {
    tokens,
    isLoading,
    fetchTokens,
    downloadPdf,
  } = useCareerPassportStore();
  const [showRevoked, setShowRevoked] = useState(false);
  const [newlyGenerated, setNewlyGenerated] = useState<string | null>(null);

  useEffect(() => {
    fetchTokens(technicianId, true);
  }, [technicianId, fetchTokens]);

  const activeTokens = tokens.filter((t) => t.is_active);
  const revokedTokens = tokens.filter((t) => !t.is_active);
  const displayedTokens = showRevoked ? tokens : activeTokens;

  const handleGenerated = (token: CareerPassportToken) => {
    setNewlyGenerated(token.id);
    setTimeout(() => setNewlyGenerated(null), 3000);
  };

  // ---- Compact layout (technician portal) ----
  if (compact) {
    return (
      <Card className="border-primary/20 bg-primary/5">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Shield className="h-5 w-5 text-primary" />
              <CardTitle className="text-base">Career Passport</CardTitle>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5 touch-manipulation"
                onClick={() => downloadPdf(technicianId, technicianName)}
              >
                <Download className="h-4 w-4" />
                <span className="hidden sm:inline">Download PDF</span>
                <span className="sm:hidden">PDF</span>
              </Button>
              <GenerateTokenDialog
                technicianId={technicianId}
                onGenerated={handleGenerated}
              />
            </div>
          </div>
          <CardDescription>
            Share your verified skills, certifications, and project history
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center py-6">
              <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            </div>
          ) : activeTokens.length === 0 ? (
            <div className="text-center py-4">
              <Link2 className="h-8 w-8 mx-auto text-muted-foreground/50 mb-2" />
              <p className="text-sm text-muted-foreground">
                No active share links
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                Generate a link to share your career passport
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {activeTokens.map((token) => (
                <CompactTokenCard
                  key={token.id}
                  token={token}
                />
              ))}
            </div>
          )}

          {revokedTokens.length > 0 && (
            <div className="mt-3 pt-2 border-t border-border">
              <button
                className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                onClick={() => setShowRevoked(!showRevoked)}
              >
                {showRevoked ? "Hide" : "Show"} {revokedTokens.length} revoked/expired link{revokedTokens.length !== 1 ? "s" : ""}
              </button>
              {showRevoked && (
                <div className="space-y-2 mt-2">
                  {revokedTokens.map((token) => (
                    <CompactTokenCard
                      key={token.id}
                      token={token}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    );
  }

  // ---- Full layout (ops TechnicianProfile tab) ----
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h3 className="text-base font-semibold flex items-center gap-2">
            <Shield className="h-4 w-4 text-primary" />
            Career Passport
          </h3>
          <p className="text-sm text-muted-foreground mt-0.5">
            Manage shareable links and download the career passport as PDF
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5"
            onClick={() => downloadPdf(technicianId, technicianName)}
          >
            <Download className="h-4 w-4" />
            Download PDF
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5"
            onClick={() =>
              window.open(`/passport/preview/${technicianId}`, "_blank")
            }
          >
            <Eye className="h-4 w-4" />
            Preview
          </Button>
          <GenerateTokenDialog
            technicianId={technicianId}
            onGenerated={handleGenerated}
          />
        </div>
      </div>

      {/* Quick Stats */}
      <div className="grid grid-cols-3 gap-3">
        <Card>
          <CardContent className="pt-4 pb-3">
            <p className="text-xs text-muted-foreground">Active Links</p>
            <p className="text-2xl font-bold text-primary">
              {activeTokens.length}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-3">
            <p className="text-xs text-muted-foreground">Revoked / Expired</p>
            <p className="text-2xl font-bold text-muted-foreground">
              {revokedTokens.length}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-3">
            <p className="text-xs text-muted-foreground">Total Generated</p>
            <p className="text-2xl font-bold">{tokens.length}</p>
          </CardContent>
        </Card>
      </div>

      {/* Newly Generated Banner */}
      {newlyGenerated && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-600 dark:text-emerald-400">
          <Check className="h-4 w-4 flex-shrink-0" />
          <p className="text-sm font-medium">
            New share link generated! Click "Copy" to share it.
          </p>
        </div>
      )}

      {/* Token Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        </div>
      ) : displayedTokens.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <Link2 className="h-12 w-12 mx-auto text-muted-foreground/30 mb-3" />
            <h4 className="text-lg font-medium text-foreground">
              No share links yet
            </h4>
            <p className="text-sm text-muted-foreground mt-1 max-w-md mx-auto">
              Generate a shareable link to give partners, recruiters, or clients
              read-only access to this technician's verified career passport.
            </p>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <div className="rounded-lg border-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Label</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead>Expires</TableHead>
                  <TableHead>Created By</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {displayedTokens.map((token) => (
                  <TokenRow key={token.id} token={token} />
                ))}
              </TableBody>
            </Table>
          </div>
        </Card>
      )}

      {/* Toggle revoked visibility */}
      {revokedTokens.length > 0 && (
        <div className="flex justify-center">
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground"
            onClick={() => setShowRevoked(!showRevoked)}
          >
            {showRevoked
              ? "Hide revoked/expired links"
              : `Show ${revokedTokens.length} revoked/expired link${revokedTokens.length !== 1 ? "s" : ""}`}
          </Button>
        </div>
      )}

      <Separator />

      {/* Info */}
      <div className="p-4 rounded-lg bg-muted/50 border">
        <h4 className="text-sm font-medium mb-2">About Career Passport</h4>
        <ul className="space-y-1.5 text-xs text-muted-foreground">
          <li className="flex items-start gap-2">
            <Shield className="h-3.5 w-3.5 mt-0.5 text-primary flex-shrink-0" />
            Shareable links provide read-only access to verified skills, certifications, and badges
          </li>
          <li className="flex items-start gap-2">
            <Clock className="h-3.5 w-3.5 mt-0.5 text-primary flex-shrink-0" />
            Links expire automatically after the set duration for security
          </li>
          <li className="flex items-start gap-2">
            <XCircle className="h-3.5 w-3.5 mt-0.5 text-primary flex-shrink-0" />
            Revoked links immediately stop working — recipients see an access denied page
          </li>
          <li className="flex items-start gap-2">
            <Download className="h-3.5 w-3.5 mt-0.5 text-primary flex-shrink-0" />
            PDF downloads are generated server-side with the latest verified data
          </li>
        </ul>
      </div>
    </div>
  );
}

export default CareerPassportPanel;
