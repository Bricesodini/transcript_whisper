import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import DashboardPage from "./pages/DashboardPage";
import DocumentsPage from "./pages/DocumentsPage";
import DocumentDetailPage from "./pages/DocumentDetailPage";
import JobsPage from "./pages/JobsPage";
import NavBar from "./components/NavBar";

function App() {
  return (
    <BrowserRouter>
      <div className="app-layout">
        <NavBar />
        <main className="content">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/docs" element={<DocumentsPage />} />
            <Route path="/docs/:docName" element={<DocumentDetailPage />} />
            <Route path="/jobs" element={<JobsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

export default App;

