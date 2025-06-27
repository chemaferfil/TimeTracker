import React, { useEffect, useState } from 'react';
import FullCalendar from '@fullcalendar/react';
import dayGridPlugin from '@fullcalendar/daygrid';

const Calendario = () => {
  const [eventos, setEventos] = useState([]);

  useEffect(() => {
    fetch('/api/ausencias') // Ajusta esta ruta según tu backend Flask
      .then(res => res.json())
      .then(data => setEventos(data));
  }, []);

  return (
    <FullCalendar
      plugins={[dayGridPlugin]}
      initialView="dayGridMonth"
      events={eventos}
      eventColor="#3788d8"
      height="auto"
    />
  );
};

export default Calendario;
