import { NavLink, Routes, Route, Navigate } from "react-router-dom";
import Transactions from "./tabs/Transactions";
import Monthly from "./tabs/Monthly";
import Performance from "./tabs/Performance";
import Research from "./tabs/Research";
import Viz from "./tabs/Viz";

export default function App() {
  return (
    <>
      <nav className="tabs">
        <NavLink to="/transactions" className={({ isActive }) => isActive ? "active" : ""}>Transactions</NavLink>
        <NavLink to="/monthly" className={({ isActive }) => isActive ? "active" : ""}>Monthly</NavLink>
        <NavLink to="/performance" className={({ isActive }) => isActive ? "active" : ""}>Performance</NavLink>
        <NavLink to="/research" className={({ isActive }) => isActive ? "active" : ""}>Research</NavLink>
        <NavLink to="/viz" className={({ isActive }) => isActive ? "active" : ""}>Visualisations</NavLink>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/transactions" replace />} />
          <Route path="/transactions" element={<Transactions />} />
          <Route path="/monthly" element={<Monthly />} />
          <Route path="/performance" element={<Performance />} />
          <Route path="/research" element={<Research />} />
          <Route path="/research/:symbol" element={<Research />} />
          <Route path="/viz" element={<Viz />} />
        </Routes>
      </main>
    </>
  );
}
