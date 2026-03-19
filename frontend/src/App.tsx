import { useEffect } from "react"
import { Routes, Route, Navigate } from "react-router-dom"
import { AppLayout } from "@/components/layout/AppLayout"
import { Dashboard } from "@/pages/Dashboard"
import { TechnicianDirectory } from "@/pages/TechnicianDirectory"
import { TechnicianProfile } from "@/pages/TechnicianProfile"
import { TrainingPipeline } from "@/pages/TrainingPipeline"
import { AgentInbox } from "@/pages/AgentInbox"
import { TechnicianPortal } from "@/pages/TechnicianPortal"
import { TimesheetSubmission } from "@/pages/TimesheetSubmission"
import { PartnerPortal } from "@/pages/PartnerPortal"
import ProjectStaffing from "@/pages/ProjectStaffing"
import { HeadcountApprovalQueue } from "@/pages/HeadcountApprovalQueue"
import { useAuthStore } from "@/stores/authStore"

function InboxPage() {
  return (
    <div className="p-6">
      <AgentInbox />
    </div>
  )
}

function TechPortalPage() {
  return (
    <div className="p-6">
      <TechnicianPortal />
    </div>
  )
}

function App() {
  const initialize = useAuthStore((s) => s.initialize)

  useEffect(() => {
    initialize()
  }, [initialize])

  return (
    <Routes>
      <Route path="/" element={<Navigate to="/ops/dashboard" replace />} />
      <Route element={<AppLayout />}>
        {/* Ops routes */}
        <Route path="/ops/dashboard" element={<div className="p-6"><Dashboard /></div>} />
        <Route path="/ops/technicians" element={<div className="p-6"><TechnicianDirectory /></div>} />
        <Route path="/ops/technicians/:id" element={<div className="p-6"><TechnicianProfile /></div>} />
        <Route path="/ops/training" element={<TrainingPipeline />} />
        <Route path="/ops/projects" element={<ProjectStaffing />} />
        <Route path="/ops/projects/:id" element={<ProjectStaffing />} />
        <Route path="/ops/inbox" element={<InboxPage />} />
        <Route path="/ops/headcount" element={<HeadcountApprovalQueue />} />

        {/* Technician routes */}
        <Route path="/tech/portal" element={<TechPortalPage />} />
        <Route path="/tech/timesheets" element={<div className="p-6"><TimesheetSubmission /></div>} />

        {/* Partner routes */}
        <Route path="/partner/portal" element={<PartnerPortal />} />
      </Route>
    </Routes>
  )
}

export default App
