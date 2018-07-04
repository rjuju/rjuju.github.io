---
layout: page
title: PostgreSQL (fr)
excerpt: "Une archive des articles de blog liés à PostgreSQL triés par date."
search_omit: true
---

<ul class="post-list">
{% for post in site.categories.postgresqlfr %}
 <li>
  <article>
   <a href="{{ site.url }}{{ post.url }}">{{ post.title }}
    <span class="entry-date">
     <time datetime="{{ post.date | date_to_xmlschema }}">
      {{ post.date | date: "%B %d, %Y" }}
     </time>
    </span>
    {% if post.excerpt %}
     <span class="excerpt">
      {{ post.excerpt }}
      <a href="{{ site.url }}{{ post.url }}" class="more">Continuer à lire</a>
     </span>
    {% endif %}
   </a>
  </article>
 </li>
{% endfor %}
</ul>
