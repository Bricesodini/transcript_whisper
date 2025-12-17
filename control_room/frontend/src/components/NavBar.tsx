import { NavLink } from "react-router-dom";

const NavBar = () => {
  return (
    <aside className="sidebar">
      <h1>Transcribe Control Room</h1>
      <nav>
        <NavLink to="/" end>
          Dashboard
        </NavLink>
        <NavLink to="/docs">Documents</NavLink>
        <NavLink to="/jobs">Jobs & Logs</NavLink>
        <NavLink to="/storage">Storage / Cleanup</NavLink>
      </nav>
    </aside>
  );
};

export default NavBar;
