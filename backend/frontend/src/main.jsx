import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";

function App() {
  const [reviews, setReviews] = useState([]);
  const [loading, setLoading] = useState(false);

  const loadReviews = async () => {
    setLoading(true);

    const res = await fetch("/reviews");
    const data = await res.json();

    setReviews(data);
    setLoading(false);
  };

  const generate = async (review) => {
    const res = await fetch("/generate", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(review),
    });

    const data = await res.json();

    alert(data.answer_text || JSON.stringify(data));
  };

  return (
    <div style={{ padding: 20 }}>
      <h1>Отзывы WB</h1>

      <button onClick={loadReviews}>
        {loading ? "Загрузка..." : "Загрузить реальные отзывы"}
      </button>

      {reviews.map((r, i) => (
        <div key={i} style={{ marginTop: 15, padding: 10, background: "#222", color: "#fff" }}>
          <div>{r.text}</div>
          <div>⭐ {r.rating}</div>

          <button onClick={() => generate(r)}>
            Сгенерировать ответ
          </button>
        </div>
      ))}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);