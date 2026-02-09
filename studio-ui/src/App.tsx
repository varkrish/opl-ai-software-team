import React from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import AppLayout from './components/AppLayout';
import Landing from './pages/Landing';
import Dashboard from './pages/Dashboard';
import Agents from './pages/Agents';
import Tasks from './pages/Tasks';
import Files from './pages/Files';
import Settings from './pages/Settings';

const App: React.FC = () => {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route element={<AppLayout />}>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/files" element={<Files />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
};

export default App;
